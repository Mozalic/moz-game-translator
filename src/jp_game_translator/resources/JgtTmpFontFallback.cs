using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using TMPro;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TextCore.LowLevel;

public static class Entrypoint
{
    public static void Main()
    {
        JgtTmpFontFallbackBootstrap.Init();
    }
}

public static class JgtTmpFontFallbackBootstrap
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
            JgtTmpFontFallbackBehaviour existing = UnityEngine.Object.FindObjectOfType<JgtTmpFontFallbackBehaviour>();
            if (existing != null)
            {
                existing.PatchNow();
                return;
            }

            GameObject host = new GameObject("JGT TMP Font Fallback");
            UnityEngine.Object.DontDestroyOnLoad(host);
            host.hideFlags = HideFlags.HideAndDontSave;
            host.AddComponent<JgtTmpFontFallbackBehaviour>().PatchNow();
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
            File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "JgtTmpFontFallback.log"), message + Environment.NewLine);
        }
        catch
        {
        }
        try
        {
            Debug.Log("[JGT TMP Font Fallback] " + message);
        }
        catch
        {
        }
    }
}

public sealed class JgtTmpFontFallbackBehaviour : MonoBehaviour
{
    private static readonly string[] FontNames = new[] { "SimHei", "Microsoft YaHei UI", "Microsoft YaHei", "SimSun", "Arial Unicode MS" };
    private static readonly string[] FontFileNames = new[] { "JgtFallbackFont.ttf", "JgtFallbackFont.ttc", "JgtFallbackFont.otf" };
    private static TMP_FontAsset fallbackFontAsset;
    private static bool fallbackFontAssetCreationAttempted;
    private static string lastPatchSummary;
    private static string requiredCharacters;
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
            yield return new WaitForSeconds(2f);
            PatchNow();
        }
    }

    private void Update()
    {
        if (Time.unscaledTime >= nextPatchAt)
        {
            nextPatchAt = Time.unscaledTime + 2f;
            PatchNow();
        }
    }

    public void PatchNow()
    {
        try
        {
            TMP_FontAsset fallback = GetFallbackFontAsset();
            if (fallback == null)
            {
                return;
            }

            int fontAssetCount = 0;
            foreach (TMP_FontAsset fontAsset in Resources.FindObjectsOfTypeAll<TMP_FontAsset>())
            {
                if (fontAsset == null || ReferenceEquals(fontAsset, fallback))
                {
                    continue;
                }
                EnsureFallback(fontAsset, fallback);
                fontAsset.isMultiAtlasTexturesEnabled = true;
                fontAssetCount++;
            }

            int textCount = 0;
            foreach (TMP_Text text in Resources.FindObjectsOfTypeAll<TMP_Text>())
            {
                if (text == null)
                {
                    continue;
                }
                if (text.font != null)
                {
                    EnsureFallback(text.font, fallback);
                }
                text.SetAllDirty();
                textCount++;
            }

            List<TMP_FontAsset> globalFallbacks = TMP_Settings.fallbackFontAssets;
            if (globalFallbacks != null && !globalFallbacks.Contains(fallback))
            {
                globalFallbacks.Insert(0, fallback);
            }

            string summary = "patched TMP fallbacks: fontAssets=" + fontAssetCount + ", textObjects=" + textCount;
            if (summary != lastPatchSummary)
            {
                lastPatchSummary = summary;
                JgtTmpFontFallbackBootstrap.Log(summary);
            }
        }
        catch (Exception ex)
        {
            JgtTmpFontFallbackBootstrap.Log("patch failed: " + ex);
        }
    }

    private static TMP_FontAsset GetFallbackFontAsset()
    {
        if (fallbackFontAsset != null)
        {
            return fallbackFontAsset;
        }
        if (fallbackFontAssetCreationAttempted)
        {
            return null;
        }
        fallbackFontAssetCreationAttempted = true;

        Font font = LoadBundledFont();
        if (font == null)
        {
            font = Font.CreateDynamicFontFromOSFont(FontNames, 90);
        }
        if (font == null)
        {
            JgtTmpFontFallbackBootstrap.Log("CreateDynamicFontFromOSFont failed");
            return null;
        }
        font.name = "JGT Chinese OS Font";

        fallbackFontAsset = TMP_FontAsset.CreateFontAsset(
            font,
            90,
            9,
            GlyphRenderMode.SDFAA,
            4096,
            4096,
            AtlasPopulationMode.Dynamic,
            true
        );
        if (fallbackFontAsset == null)
        {
            JgtTmpFontFallbackBootstrap.Log("TMP_FontAsset.CreateFontAsset failed");
            return null;
        }

        fallbackFontAsset.name = "JGT Chinese TMP Dynamic Fallback";
        fallbackFontAsset.isMultiAtlasTexturesEnabled = true;

        string chars = GetRequiredCharacters();
        if (!string.IsNullOrEmpty(chars))
        {
            string missing;
            fallbackFontAsset.TryAddCharacters(chars, out missing);
            if (!string.IsNullOrEmpty(missing))
            {
                JgtTmpFontFallbackBootstrap.Log("missing chars after preload: " + missing.Length);
            }
        }

        return fallbackFontAsset;
    }

    private static Font LoadBundledFont()
    {
        foreach (string path in CandidateFontPaths())
        {
            try
            {
                if (!File.Exists(path))
                {
                    continue;
                }
                Font font = new Font(path);
                if (font != null)
                {
                    font.name = "JGT Chinese Bundled Font";
                    JgtTmpFontFallbackBootstrap.Log("loaded bundled font: " + path);
                    return font;
                }
            }
            catch (Exception ex)
            {
                JgtTmpFontFallbackBootstrap.Log("load bundled font failed: " + path + " " + ex.Message);
            }
        }
        return null;
    }

    private static IEnumerable<string> CandidateFontPaths()
    {
        string managedBase = AppDomain.CurrentDomain.BaseDirectory;
        string gameRoot = Path.GetFullPath(Path.Combine(managedBase, "..", ".."));
        foreach (string fileName in FontFileNames)
        {
            yield return Path.Combine(managedBase, "JgtTmpFontFallback", fileName);
            yield return Path.Combine(gameRoot, "JgtTmpFontFallback", fileName);
        }
    }

    private static string GetRequiredCharacters()
    {
        if (requiredCharacters != null)
        {
            return requiredCharacters;
        }

        try
        {
            string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "JgtTmpFontFallback", "chars.txt");
            if (File.Exists(path))
            {
                requiredCharacters = File.ReadAllText(path);
            }
            else
            {
                string managedBase = AppDomain.CurrentDomain.BaseDirectory;
                string gameRoot = Path.GetFullPath(Path.Combine(managedBase, "..", ".."));
                string gameRootPath = Path.Combine(gameRoot, "JgtTmpFontFallback", "chars.txt");
                requiredCharacters = File.Exists(gameRootPath) ? File.ReadAllText(gameRootPath) : string.Empty;
            }
        }
        catch
        {
            requiredCharacters = string.Empty;
        }
        return requiredCharacters;
    }

    private static void EnsureFallback(TMP_FontAsset fontAsset, TMP_FontAsset fallback)
    {
        if (fontAsset.fallbackFontAssetTable == null)
        {
            fontAsset.fallbackFontAssetTable = new List<TMP_FontAsset>();
        }
        if (!fontAsset.fallbackFontAssetTable.Contains(fallback))
        {
            fontAsset.fallbackFontAssetTable.Insert(0, fallback);
        }
    }
}
