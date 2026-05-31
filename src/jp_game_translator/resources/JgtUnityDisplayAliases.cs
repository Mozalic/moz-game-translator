using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class Entrypoint
{
    public static void Main()
    {
        JgtUnityDisplayAliasesBootstrap.Init();
    }
}

public static class JgtUnityDisplayAliasesBootstrap
{
    private static bool initialized;

    public static void Init()
    {
        if (initialized)
        {
            return;
        }
        initialized = true;
        SceneManager.sceneLoaded += OnSceneLoaded;
    }

    public static void InstallFromGame()
    {
        Init();
        InstallBehaviour();
    }

    private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        InstallBehaviour();
    }

    private static void InstallBehaviour()
    {
        try
        {
            JgtUnityDisplayAliasesBehaviour existing = UnityEngine.Object.FindObjectOfType<JgtUnityDisplayAliasesBehaviour>();
            if (existing != null)
            {
                existing.PatchNow();
                return;
            }

            GameObject host = new GameObject("JGT Unity Display Aliases");
            UnityEngine.Object.DontDestroyOnLoad(host);
            host.hideFlags = HideFlags.HideAndDontSave;
            host.AddComponent<JgtUnityDisplayAliasesBehaviour>().PatchNow();
        }
        catch (Exception ex)
        {
            Log("install failed: " + ex);
        }
    }

    public static void Log(string message)
    {
        try
        {
            File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "JgtUnityDisplayAliases.log"), message + Environment.NewLine);
        }
        catch
        {
        }
        try
        {
            Debug.Log("[JGT Unity Display Aliases] " + message);
        }
        catch
        {
        }
    }
}

public sealed class JgtUnityDisplayAliasesBehaviour : MonoBehaviour
{
    private static Dictionary<string, string> aliases;
    private static bool aliasesLoaded;
    private static string lastPatchSummary;
    private float nextPatchAt;

    private void Awake()
    {
        DontDestroyOnLoad(gameObject);
    }

    private IEnumerator Start()
    {
        yield return null;
        PatchNow();
        while (true)
        {
            yield return new WaitForSeconds(1f);
            PatchNow();
        }
    }

    private void Update()
    {
        if (Time.unscaledTime >= nextPatchAt)
        {
            nextPatchAt = Time.unscaledTime + 1f;
            PatchNow();
        }
    }

    public void PatchNow()
    {
        try
        {
            Dictionary<string, string> map = GetAliases();
            if (map.Count == 0)
            {
                return;
            }

            int scanned = 0;
            int changed = 0;
            foreach (MonoBehaviour behaviour in Resources.FindObjectsOfTypeAll<MonoBehaviour>())
            {
                if (behaviour == null)
                {
                    continue;
                }
                if (!IsSceneObject(behaviour))
                {
                    continue;
                }
                Type type = behaviour.GetType();
                if (!IsTextComponent(type))
                {
                    continue;
                }

                PropertyInfo property = type.GetProperty("text", BindingFlags.Instance | BindingFlags.Public);
                if (property == null || !property.CanRead || !property.CanWrite || property.PropertyType != typeof(string))
                {
                    continue;
                }

                scanned++;
                string current = property.GetValue(behaviour, null) as string;
                string translated = TranslateExact(current, map);
                if (!string.Equals(current, translated, StringComparison.Ordinal))
                {
                    property.SetValue(behaviour, translated, null);
                    changed++;
                }
            }

            string summary = "patched display aliases: aliases=" + map.Count + ", textObjects=" + scanned + ", changed=" + changed;
            if (changed > 0 || summary != lastPatchSummary)
            {
                lastPatchSummary = summary;
                JgtUnityDisplayAliasesBootstrap.Log(summary);
            }
        }
        catch (Exception ex)
        {
            JgtUnityDisplayAliasesBootstrap.Log("patch failed: " + ex);
        }
    }

    private static bool IsSceneObject(MonoBehaviour behaviour)
    {
        try
        {
            return behaviour.gameObject != null && behaviour.gameObject.scene.IsValid();
        }
        catch
        {
            return false;
        }
    }

    private static bool IsTextComponent(Type type)
    {
        for (Type current = type; current != null; current = current.BaseType)
        {
            string fullName = current.FullName;
            if (fullName == "TMPro.TMP_Text"
                || fullName == "TMPro.TextMeshProUGUI"
                || fullName == "TMPro.TextMeshPro"
                || fullName == "UnityEngine.UI.Text")
            {
                return true;
            }
        }
        return false;
    }

    private static string TranslateExact(string value, Dictionary<string, string> map)
    {
        if (string.IsNullOrEmpty(value))
        {
            return value;
        }

        string direct;
        if (map.TryGetValue(value, out direct))
        {
            return direct;
        }

        int start = 0;
        int end = value.Length;
        while (start < end && char.IsWhiteSpace(value[start]))
        {
            start++;
        }
        while (end > start && char.IsWhiteSpace(value[end - 1]))
        {
            end--;
        }
        if (start == 0 && end == value.Length)
        {
            return value;
        }

        string core = value.Substring(start, end - start);
        string target;
        if (!map.TryGetValue(core, out target))
        {
            return value;
        }
        return value.Substring(0, start) + target + value.Substring(end);
    }

    private static Dictionary<string, string> GetAliases()
    {
        if (aliasesLoaded)
        {
            return aliases;
        }
        aliasesLoaded = true;
        aliases = LoadAliases();
        JgtUnityDisplayAliasesBootstrap.Log("loaded display aliases: " + aliases.Count);
        return aliases;
    }

    private static Dictionary<string, string> LoadAliases()
    {
        Dictionary<string, string> result = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (string path in CandidateAliasPaths())
        {
            try
            {
                if (!File.Exists(path))
                {
                    continue;
                }
                foreach (string line in File.ReadAllLines(path, Encoding.UTF8))
                {
                    if (string.IsNullOrWhiteSpace(line))
                    {
                        continue;
                    }
                    string[] parts = line.Split('\t');
                    if (parts.Length < 2)
                    {
                        continue;
                    }
                    string source = FromBase64(parts[0]).Trim();
                    string target = FromBase64(parts[1]).Trim();
                    if (source.Length == 0 || target.Length == 0 || source == target)
                    {
                        continue;
                    }
                    if (!result.ContainsKey(source))
                    {
                        result.Add(source, target);
                    }
                }
                if (result.Count > 0)
                {
                    JgtUnityDisplayAliasesBootstrap.Log("loaded aliases from: " + path);
                    return result;
                }
            }
            catch (Exception ex)
            {
                JgtUnityDisplayAliasesBootstrap.Log("load aliases failed: " + path + " " + ex.Message);
            }
        }
        return result;
    }

    private static IEnumerable<string> CandidateAliasPaths()
    {
        string managedBase = AppDomain.CurrentDomain.BaseDirectory;
        string gameRoot = Path.GetFullPath(Path.Combine(managedBase, "..", ".."));
        yield return Path.Combine(managedBase, "JgtUnityDisplayAliases", "display_aliases.tsv");
        yield return Path.Combine(gameRoot, "JgtUnityDisplayAliases", "display_aliases.tsv");
    }

    private static string FromBase64(string value)
    {
        return Encoding.UTF8.GetString(Convert.FromBase64String(value));
    }
}
