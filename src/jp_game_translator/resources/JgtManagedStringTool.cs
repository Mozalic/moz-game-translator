using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Mono.Cecil;
using Mono.Cecil.Cil;

public static class JgtManagedStringTool
{
    public static int Main(string[] args)
    {
        if (args.Length < 3)
        {
            Console.Error.WriteLine("usage: JgtManagedStringTool dump <assembly.dll> <output.tsv> | patch <assembly.dll> <translations.tsv>");
            return 2;
        }

        string command = args[0];
        string assemblyPath = Path.GetFullPath(args[1]);
        string dataPath = Path.GetFullPath(args[2]);
        if (command == "dump")
        {
            return Dump(assemblyPath, dataPath);
        }
        if (command == "patch")
        {
            return Patch(assemblyPath, dataPath);
        }

        Console.Error.WriteLine("unknown command: " + command);
        return 2;
    }

    private static int Dump(string assemblyPath, string outputPath)
    {
        using (AssemblyDefinition assembly = ReadAssembly(assemblyPath))
        using (StreamWriter writer = new StreamWriter(outputPath, false, new UTF8Encoding(false)))
        {
            foreach (MethodDefinition method in AllTypes(assembly.MainModule).SelectMany(type => type.Methods))
            {
                if (!method.HasBody)
                {
                    continue;
                }
                for (int index = 0; index < method.Body.Instructions.Count; index++)
                {
                    Instruction instruction = method.Body.Instructions[index];
                    if (instruction.OpCode.Code != Code.Ldstr)
                    {
                        continue;
                    }
                    string value = instruction.Operand as string;
                    if (string.IsNullOrEmpty(value))
                    {
                        continue;
                    }
                    string context = method.DeclaringType.FullName + "::" + method.Name;
                    writer.Write(method.MetadataToken.ToInt32());
                    writer.Write('\t');
                    writer.Write(index);
                    writer.Write('\t');
                    writer.Write(Base64(value));
                    writer.Write('\t');
                    writer.Write(Base64(context));
                    writer.WriteLine();
                }
            }
        }
        return 0;
    }

    private static int Patch(string assemblyPath, string translationsPath)
    {
        List<PatchRecord> records = File.ReadAllLines(translationsPath, Encoding.UTF8)
            .Where(line => !string.IsNullOrWhiteSpace(line))
            .Select(ParseRecord)
            .ToList();
        if (records.Count == 0)
        {
            Console.WriteLine("no records");
            return 0;
        }

        using (AssemblyDefinition assembly = ReadAssembly(assemblyPath))
        {
            Dictionary<int, MethodDefinition> methods = AllTypes(assembly.MainModule)
                .SelectMany(type => type.Methods)
                .Where(method => method.HasBody)
                .ToDictionary(method => method.MetadataToken.ToInt32());
            int patched = 0;
            int skipped = 0;
            foreach (PatchRecord record in records)
            {
                MethodDefinition method;
                if (!methods.TryGetValue(record.MethodToken, out method))
                {
                    skipped++;
                    continue;
                }
                Instruction instruction = null;
                if (record.InstructionIndex >= 0 && record.InstructionIndex < method.Body.Instructions.Count)
                {
                    Instruction candidate = method.Body.Instructions[record.InstructionIndex];
                    if (candidate.OpCode.Code == Code.Ldstr && string.Equals(candidate.Operand as string, record.Source, StringComparison.Ordinal))
                    {
                        instruction = candidate;
                    }
                }
                if (instruction == null)
                {
                    instruction = method.Body.Instructions
                        .FirstOrDefault(item => item.OpCode.Code == Code.Ldstr && string.Equals(item.Operand as string, record.Source, StringComparison.Ordinal));
                }
                if (instruction == null || string.IsNullOrEmpty(record.Target))
                {
                    skipped++;
                    continue;
                }
                instruction.Operand = record.Target;
                patched++;
            }

            if (patched > 0)
            {
                string tempPath = assemblyPath + ".jgt_strings_tmp";
                assembly.Write(tempPath);
                File.Copy(tempPath, assemblyPath, true);
                File.Delete(tempPath);
            }
            Console.WriteLine("patched=" + patched + " skipped=" + skipped);
            return 0;
        }
    }

    private static PatchRecord ParseRecord(string line)
    {
        string[] parts = line.Split('\t');
        if (parts.Length < 4)
        {
            throw new InvalidDataException("invalid record: " + line);
        }
        return new PatchRecord
        {
            MethodToken = int.Parse(parts[0]),
            InstructionIndex = int.Parse(parts[1]),
            Source = FromBase64(parts[2]),
            Target = FromBase64(parts[3])
        };
    }

    private static AssemblyDefinition ReadAssembly(string assemblyPath)
    {
        string managedDir = Path.GetDirectoryName(assemblyPath);
        DefaultAssemblyResolver resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(managedDir);
        return AssemblyDefinition.ReadAssembly(
            assemblyPath,
            new ReaderParameters
            {
                AssemblyResolver = resolver,
                ReadWrite = false,
                InMemory = true
            }
        );
    }

    private static IEnumerable<TypeDefinition> AllTypes(ModuleDefinition module)
    {
        foreach (TypeDefinition type in module.Types)
        {
            foreach (TypeDefinition nested in AllTypes(type))
            {
                yield return nested;
            }
        }
    }

    private static IEnumerable<TypeDefinition> AllTypes(TypeDefinition type)
    {
        yield return type;
        foreach (TypeDefinition nestedType in type.NestedTypes)
        {
            foreach (TypeDefinition nested in AllTypes(nestedType))
            {
                yield return nested;
            }
        }
    }

    private static string Base64(string value)
    {
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(value));
    }

    private static string FromBase64(string value)
    {
        return Encoding.UTF8.GetString(Convert.FromBase64String(value));
    }

    private sealed class PatchRecord
    {
        public int MethodToken;
        public int InstructionIndex;
        public string Source;
        public string Target;
    }
}
