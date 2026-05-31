using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

public static class JgtManagedAssemblyPatcher
{
    private const string DefaultBootstrapTypeName = "JgtTmpFontFallbackBootstrap";
    private const string DefaultInstallMethodName = "InstallFromGame";

    public static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: JgtManagedAssemblyPatcher <Assembly-CSharp.dll> <runtime.dll> [bootstrapType] [installMethod]");
            return 2;
        }

        string assemblyPath = Path.GetFullPath(args[0]);
        string fallbackPath = Path.GetFullPath(args[1]);
        string bootstrapTypeName = args.Length >= 3 ? args[2] : DefaultBootstrapTypeName;
        string installMethodName = args.Length >= 4 ? args[3] : DefaultInstallMethodName;
        string managedDir = Path.GetDirectoryName(assemblyPath);
        string fallbackDir = Path.GetDirectoryName(fallbackPath);

        DefaultAssemblyResolver resolver = new DefaultAssemblyResolver();
        resolver.AddSearchDirectory(managedDir);
        resolver.AddSearchDirectory(fallbackDir);

        ReaderParameters readerParameters = new ReaderParameters
        {
            AssemblyResolver = resolver,
            ReadWrite = false,
            InMemory = true
        };

        using (AssemblyDefinition assembly = AssemblyDefinition.ReadAssembly(assemblyPath, readerParameters))
        using (AssemblyDefinition fallbackAssembly = AssemblyDefinition.ReadAssembly(fallbackPath, readerParameters))
        {
            MethodDefinition installMethod = FindInstallMethod(fallbackAssembly, bootstrapTypeName, installMethodName);
            ModuleDefinition module = assembly.MainModule;

            List<MethodDefinition> targets = FindPatchTargets(module);
            if (targets.Count == 0)
            {
                Console.Error.WriteLine("no suitable Awake/Start target found");
                return 3;
            }

            MethodReference importedInstallMethod = module.ImportReference(installMethod);

            List<string> patchedTargets = new List<string>();
            foreach (MethodDefinition target in targets)
            {
                if (HasInstallCall(target, bootstrapTypeName, installMethodName))
                {
                    continue;
                }

                ILProcessor il = target.Body.GetILProcessor();
                Instruction call = il.Create(OpCodes.Call, importedInstallMethod);
                if (target.Body.Instructions.Count == 0)
                {
                    il.Append(call);
                    il.Append(il.Create(OpCodes.Ret));
                }
                else
                {
                    il.InsertBefore(target.Body.Instructions[0], call);
                }
                patchedTargets.Add(target.DeclaringType.FullName + "::" + target.Name);
            }

            if (patchedTargets.Count == 0)
            {
                Console.WriteLine("already patched");
                return 0;
            }

            string tempPath = assemblyPath + ".jgt_tmp";
            assembly.Write(tempPath);
            File.Copy(tempPath, assemblyPath, true);
            File.Delete(tempPath);

            Console.WriteLine("patched " + string.Join(", ", patchedTargets.ToArray()));
            return 0;
        }
    }

    private static MethodDefinition FindInstallMethod(
        AssemblyDefinition fallbackAssembly,
        string bootstrapTypeName,
        string installMethodName)
    {
        TypeDefinition bootstrapType = AllTypes(fallbackAssembly.MainModule)
            .FirstOrDefault(type => type.FullName == bootstrapTypeName || type.Name == bootstrapTypeName);
        if (bootstrapType == null)
        {
            throw new InvalidOperationException("bootstrap type not found: " + bootstrapTypeName);
        }

        MethodDefinition installMethod = bootstrapType.Methods.FirstOrDefault(
            method => method.Name == installMethodName && method.IsStatic && method.Parameters.Count == 0);
        if (installMethod == null)
        {
            throw new InvalidOperationException("install method not found: " + installMethodName);
        }

        return installMethod;
    }

    private static bool HasInstallCall(MethodDefinition method, string bootstrapTypeName, string installMethodName)
    {
        if (!method.HasBody)
        {
            return false;
        }

        foreach (Instruction instruction in method.Body.Instructions)
        {
            MethodReference methodReference = instruction.Operand as MethodReference;
            if (methodReference == null)
            {
                continue;
            }
            if (methodReference.Name == installMethodName
                && (methodReference.DeclaringType.FullName == bootstrapTypeName
                    || methodReference.DeclaringType.Name == bootstrapTypeName))
            {
                return true;
            }
        }
        return false;
    }

    private static List<MethodDefinition> FindPatchTargets(ModuleDefinition module)
    {
        string[,] preferredTargets = new string[,]
        {
            { "GameDataManager", "Awake" },
            { "UIManager", "Awake" },
            { "DialogManager", "Awake" },
            { "TitleSceneManager", "Awake" },
            { "GameDataManager", "Start" },
            { "UIManager", "Start" },
            { "DialogManager", "Start" },
            { "TitleSceneManager", "Start" }
        };

        List<MethodDefinition> targets = new List<MethodDefinition>();
        HashSet<string> seen = new HashSet<string>();
        List<TypeDefinition> types = AllTypes(module).ToList();
        for (int index = 0; index < preferredTargets.GetLength(0); index++)
        {
            string typeName = preferredTargets[index, 0];
            string methodName = preferredTargets[index, 1];
            MethodDefinition preferred = types
                .Where(type => type.Name == typeName || type.FullName.EndsWith("." + typeName, StringComparison.Ordinal))
                .SelectMany(type => type.Methods)
                .FirstOrDefault(method => IsLifecycleMethod(method, methodName));
            if (preferred != null)
            {
                AddTarget(targets, seen, preferred);
            }
        }

        foreach (MethodDefinition awake in types
            .Where(IsGameType)
            .SelectMany(type => type.Methods)
            .Where(method => IsLifecycleMethod(method, "Awake"))
            .Take(8))
        {
            AddTarget(targets, seen, awake);
        }

        foreach (MethodDefinition start in types
            .Where(IsGameType)
            .SelectMany(type => type.Methods)
            .Where(method => IsLifecycleMethod(method, "Start"))
            .Take(8))
        {
            AddTarget(targets, seen, start);
        }

        return targets;
    }

    private static void AddTarget(List<MethodDefinition> targets, HashSet<string> seen, MethodDefinition method)
    {
        string key = method.MetadataToken.ToInt32().ToString();
        if (seen.Add(key))
        {
            targets.Add(method);
        }
    }

    private static bool IsLifecycleMethod(MethodDefinition method, string methodName)
    {
        return method.Name == methodName
            && method.HasBody
            && !method.IsConstructor
            && method.Parameters.Count == 0
            && method.ReturnType.FullName == "System.Void";
    }

    private static bool IsGameType(TypeDefinition type)
    {
        return !type.FullName.StartsWith("TMPro.", StringComparison.Ordinal)
            && !type.FullName.StartsWith("UnityEngine.", StringComparison.Ordinal)
            && !type.FullName.StartsWith("Unity.", StringComparison.Ordinal);
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
}
