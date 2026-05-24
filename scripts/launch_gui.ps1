param()

Set-StrictMode -Version 2.0
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:SrcRoot = Join-Path $script:RepoRoot "src"
$script:Entries = @()
$script:Glossary = @()
$script:Busy = $false
$script:CurrentTaskTitle = ""
$script:CurrentTaskOnSuccess = $null
$script:Translations = $null
$script:CurrentLanguage = "zh-Hans"
$script:L10nControls = New-Object System.Collections.ArrayList
$script:L10nColumns = New-Object System.Collections.ArrayList
$script:TextAliases = @{
    "Japanese Game Translator" = "app_title"
    "Language" = "language"
    "Game Dir" = "game_dir"
    "Workspace" = "workspace"
    "Output Dir" = "output_dir"
    "Browse" = "browse"
    "Choose game directory" = "choose_game_dir"
    "Choose workspace" = "choose_workspace"
    "Choose output directory" = "choose_output_dir"
    "Choose provider config" = "choose_provider_config"
    "Workflow" = "workflow"
    "Entries" = "entries"
    "Glossary" = "glossary"
    "Open-source Tools" = "open_source_tools"
    "Detect" = "detect"
    "Extract" = "extract"
    "Load Workspace" = "load_workspace"
    "Build Terms" = "build_terms"
    "Translate Batch" = "translate_batch"
    "Apply" = "apply"
    "Config" = "config"
    "Provider" = "provider"
    "Target" = "target"
    "Batch" = "batch"
    "Overwrite existing output directory" = "overwrite"
    "Term min count" = "term_min_count"
    "Candidate limit" = "candidate_limit"
    "Search" = "search"
    "Save" = "save"
    "Refresh" = "refresh"
    "Save All" = "save_all"
    "Status" = "status"
    "Source" = "source"
    "Translation" = "translation"
    "File" = "file"
    "Term" = "term_source"
    "Term Target" = "term_target"
    "Count" = "count"
    "Note" = "note"
    "Approve" = "approve"
    "Reject" = "reject"
    "Pending" = "pending"
    "Tool" = "tool"
    "Engines" = "engines"
    "Role" = "role"
    "License" = "license"
    "Open Homepage" = "open_homepage"
    "Ready" = "ready"
}

function New-Font($Size, $Style = [System.Drawing.FontStyle]::Regular) {
    return New-Object System.Drawing.Font("Microsoft YaHei UI", $Size, $Style)
}

function Load-Translations() {
    $path = Join-Path $script:SrcRoot "jp_game_translator\resources\gui_i18n.json"
    if (Test-Path $path) {
        $script:Translations = Get-Content -LiteralPath $path -Encoding UTF8 -Raw | ConvertFrom-Json
    }
}

function T([string]$Key) {
    if ($null -eq $script:Translations) {
        return $Key
    }

    if ($script:TextAliases.ContainsKey($Key)) {
        $Key = $script:TextAliases[$Key]
    }

    $languageTable = $null
    $languageProperty = $script:Translations.PSObject.Properties[$script:CurrentLanguage]
    if ($null -ne $languageProperty) {
        $languageTable = $languageProperty.Value
    }
    if ($null -ne $languageTable) {
        $property = $languageTable.PSObject.Properties[$Key]
        if ($null -ne $property) {
            return [string]$property.Value
        }
    }

    $fallbackProperty = $script:Translations.PSObject.Properties["en"]
    if ($null -ne $fallbackProperty) {
        $fallback = $fallbackProperty.Value.PSObject.Properties[$Key]
        if ($null -ne $fallback) {
            return [string]$fallback.Value
        }
    }

    return $Key
}

function Register-ControlText($Control, [string]$Key) {
    [void]$script:L10nControls.Add([pscustomobject]@{ Control = $Control; Key = $Key })
    $Control.Text = T $Key
    return $Control
}

function Register-ColumnText($Column, [string]$Key) {
    [void]$script:L10nColumns.Add([pscustomobject]@{ Column = $Column; Key = $Key })
    $Column.HeaderText = T $Key
    return $Column
}

function Apply-Localization() {
    foreach ($item in $script:L10nControls) {
        $item.Control.Text = T $item.Key
    }
    foreach ($item in $script:L10nColumns) {
        $item.Column.HeaderText = T $item.Key
    }
    if ($null -ne $statusLabel -and !$script:Busy) {
        $statusLabel.Text = T "ready"
    }
}

function Quote-Arg([string]$Value) {
    if ($null -eq $Value) {
        return '""'
    }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Invoke-JgtProcess([string[]]$ArgsList) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $allArgs = @("-m", "jp_game_translator") + $ArgsList
    $psi.Arguments = ($allArgs | ForEach-Object { Quote-Arg $_ }) -join " "
    $psi.WorkingDirectory = $script:RepoRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.EnvironmentVariables["PYTHONPATH"] = $script:SrcRoot

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Output = ($stdout + $stderr).Trim()
    }
}

function Append-Log([string]$Message) {
    if ([string]::IsNullOrWhiteSpace($Message)) {
        return
    }
    $logBox.AppendText($Message + [Environment]::NewLine)
    $logBox.SelectionStart = $logBox.TextLength
    $logBox.ScrollToCaret()
}

function Set-Busy([bool]$Value, [string]$Text) {
    $script:Busy = $Value
    $statusLabel.Text = $Text
    if ($Value) {
        $progress.Style = [System.Windows.Forms.ProgressBarStyle]::Marquee
    } else {
        $progress.Style = [System.Windows.Forms.ProgressBarStyle]::Blocks
        $progress.Value = 0
    }
}

function Start-JgtTask([string]$Title, [string[]]$ArgsList, [scriptblock]$OnSuccess) {
    if ($script:Busy) {
        [System.Windows.Forms.MessageBox]::Show((T "busy_message"), (T "busy"), "OK", "Warning") | Out-Null
        return
    }

    $script:CurrentTaskTitle = $Title
    $script:CurrentTaskOnSuccess = $OnSuccess
    Set-Busy $true ($Title + (T "running_suffix"))
    Append-Log ($Title + (T "started_suffix"))

    $worker = New-Object System.ComponentModel.BackgroundWorker
    $worker.DoWork += {
        param($sender, $eventArgs)
        $eventArgs.Result = Invoke-JgtProcess ([string[]]$eventArgs.Argument)
    }
    $worker.RunWorkerCompleted += {
        param($sender, $eventArgs)
        Set-Busy $false ($script:CurrentTaskTitle + (T "done_suffix"))
        if ($eventArgs.Error) {
            Append-Log $eventArgs.Error.ToString()
            [System.Windows.Forms.MessageBox]::Show($eventArgs.Error.Message, $script:CurrentTaskTitle + (T "failed_suffix"), "OK", "Error") | Out-Null
            return
        }

        $result = $eventArgs.Result
        if ($result.Output) {
            Append-Log $result.Output
        }
        if ($result.ExitCode -ne 0) {
            [System.Windows.Forms.MessageBox]::Show($result.Output, $script:CurrentTaskTitle + (T "failed_suffix"), "OK", "Error") | Out-Null
            return
        }

        if ($script:CurrentTaskOnSuccess) {
            & $script:CurrentTaskOnSuccess $result.Output
        }
    }
    $worker.RunWorkerAsync($ArgsList)
}

function Require-Text([System.Windows.Forms.TextBox]$TextBox, [string]$Label) {
    $value = $TextBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw ((T "please_choose") -f $Label)
    }
    return $value
}

function Set-Default-ProjectPaths([string]$GameDir) {
    if ([string]::IsNullOrWhiteSpace($workspaceBox.Text)) {
        $workspaceBox.Text = Join-Path (Join-Path $script:RepoRoot "work") (Split-Path $GameDir -Leaf)
    }
    if ([string]::IsNullOrWhiteSpace($outputBox.Text)) {
        $parent = Split-Path $GameDir -Parent
        $leaf = Split-Path $GameDir -Leaf
        $outputBox.Text = Join-Path $parent ($leaf + "_zh")
    }
}

function Browse-Folder([System.Windows.Forms.TextBox]$Target, [string]$Title) {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = T $Title
    if (![string]::IsNullOrWhiteSpace($Target.Text) -and (Test-Path -LiteralPath $Target.Text)) {
        $dialog.SelectedPath = $Target.Text
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $Target.Text = $dialog.SelectedPath
        if ($Target -eq $gameBox) {
            Set-Default-ProjectPaths $dialog.SelectedPath
        }
    }
}

function Browse-File([System.Windows.Forms.TextBox]$Target, [string]$Title) {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = T $Title
    $dialog.Filter = "JSON files (*.json)|*.json|All files (*.*)|*.*"
    if (![string]::IsNullOrWhiteSpace($Target.Text) -and (Test-Path -LiteralPath $Target.Text)) {
        $dialog.FileName = $Target.Text
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $Target.Text = $dialog.FileName
    }
}

function Read-JsonLines([string]$Path) {
    if (!(Test-Path $Path)) {
        return @()
    }
    $items = New-Object System.Collections.ArrayList
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        if (![string]::IsNullOrWhiteSpace($_)) {
            [void]$items.Add(($_ | ConvertFrom-Json))
        }
    }
    return @($items)
}

function Write-JsonLines([string]$Path, [object[]]$Items) {
    $lines = @()
    foreach ($item in $Items) {
        $lines += ($item | ConvertTo-Json -Compress -Depth 30)
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $lines, $encoding)
}

function Get-WorkspacePath() {
    return Require-Text $workspaceBox (T "workspace")
}

function Load-WorkspaceData() {
    try {
        $workspace = Get-WorkspacePath
        $entriesPath = Join-Path $workspace "entries.jsonl"
        $glossaryPath = Join-Path $workspace "glossary.tsv"
        $script:Entries = Read-JsonLines $entriesPath
        Load-GlossaryData $glossaryPath
        Refresh-EntriesGrid
        Refresh-GlossaryGrid
        Append-Log ((T "loaded_workspace") -f $workspace, $script:Entries.Count, $script:Glossary.Count)
        $statusLabel.Text = T "workspace_loaded"
    } catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "load_failed"), "OK", "Error") | Out-Null
    }
}

function Load-GlossaryData([string]$Path) {
    if (!(Test-Path $Path)) {
        $script:Glossary = @()
        return
    }
    $script:Glossary = @(Import-Csv -LiteralPath $Path -Delimiter "`t" -Encoding UTF8)
}

function ConvertTo-TsvField([string]$Value) {
    if ($null -eq $Value) {
        $Value = ""
    }
    $needsQuote = $Value.Contains("`t") -or $Value.Contains("`r") -or $Value.Contains("`n") -or $Value.Contains('"')
    $escaped = $Value.Replace('"', '""')
    if ($needsQuote) {
        return '"' + $escaped + '"'
    }
    return $escaped
}

function Save-GlossaryData() {
    try {
        $workspace = Get-WorkspacePath
        $path = Join-Path $workspace "glossary.tsv"
        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add("source`ttarget`tstatus`tnote`tcount")
        foreach ($term in $script:Glossary) {
            $fields = @(
                (ConvertTo-TsvField $term.source),
                (ConvertTo-TsvField $term.target),
                (ConvertTo-TsvField $term.status),
                (ConvertTo-TsvField $term.note),
                (ConvertTo-TsvField ([string]$term.count))
            )
            $lines.Add(($fields -join "`t"))
        }
        $encoding = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($path, $lines, $encoding)
        Append-Log ((T "saved_glossary") -f $path)
        Refresh-GlossaryGrid
    } catch {
        [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "save_failed"), "OK", "Error") | Out-Null
    }
}

function One-Line([string]$Text, [int]$Limit = 120) {
    if ($null -eq $Text) {
        return ""
    }
    $value = $Text.Replace("`r", "\r").Replace("`n", "\n")
    if ($value.Length -gt $Limit) {
        return $value.Substring(0, $Limit - 1) + "..."
    }
    return $value
}

function Refresh-EntriesGrid() {
    $entriesGrid.Rows.Clear()
    $query = $entrySearchBox.Text.Trim().ToLowerInvariant()
    foreach ($entry in $script:Entries) {
        $translation = ""
        if ($null -ne $entry.translation) {
            $translation = [string]$entry.translation
        }
        $haystack = (($entry.source, $translation, $entry.file, $entry.status) -join "`n").ToLowerInvariant()
        if ($query -and !$haystack.Contains($query)) {
            continue
        }
        $rowIndex = $entriesGrid.Rows.Add($entry.status, (One-Line $entry.source), (One-Line $translation), $entry.file)
        $entriesGrid.Rows[$rowIndex].Tag = $entry.id
    }
}

function Refresh-GlossaryGrid() {
    $glossaryGrid.Rows.Clear()
    $query = $termSearchBox.Text.Trim().ToLowerInvariant()
    foreach ($term in $script:Glossary) {
        $haystack = (($term.source, $term.target, $term.status, $term.note) -join "`n").ToLowerInvariant()
        if ($query -and !$haystack.Contains($query)) {
            continue
        }
        $rowIndex = $glossaryGrid.Rows.Add($term.source, $term.target, $term.status, $term.count)
        $glossaryGrid.Rows[$rowIndex].Tag = $term.source
    }
}

function Find-EntryById([string]$Id) {
    foreach ($entry in $script:Entries) {
        if ($entry.id -eq $Id) {
            return $entry
        }
    }
    return $null
}

function Find-TermBySource([string]$Source) {
    foreach ($term in $script:Glossary) {
        if ($term.source -eq $Source) {
            return $term
        }
    }
    return $null
}

function Save-SelectedEntry() {
    if ($entriesGrid.SelectedRows.Count -eq 0) {
        return
    }
    $id = [string]$entriesGrid.SelectedRows[0].Tag
    $entry = Find-EntryById $id
    if ($null -eq $entry) {
        return
    }
    $entry.translation = $entryTranslationBox.Text
    $entry.status = $entryStatusBox.Text
    $path = Join-Path (Get-WorkspacePath) "entries.jsonl"
    Write-JsonLines $path $script:Entries
    Refresh-EntriesGrid
    Append-Log ((T "saved_entry") -f $id)
}

function Save-SelectedTerm() {
    if ($glossaryGrid.SelectedRows.Count -eq 0) {
        return
    }
    $source = [string]$glossaryGrid.SelectedRows[0].Tag
    $term = Find-TermBySource $source
    if ($null -eq $term) {
        return
    }
    $term.target = $termTargetBox.Text.Trim()
    $term.status = $termStatusBox.Text
    $term.note = $termNoteBox.Text
    Save-GlossaryData
}

function New-Label([string]$Text) {
    $label = New-Object System.Windows.Forms.Label
    $label.Dock = "Fill"
    $label.TextAlign = "MiddleLeft"
    $label.Font = New-Font 9
    return Register-ControlText $label $Text
}

function New-Button([string]$Text, [scriptblock]$Click) {
    $button = New-Object System.Windows.Forms.Button
    $button.Dock = "Fill"
    $button.Height = 34
    $button.Font = New-Font 9
    $button.Add_Click($Click)
    return Register-ControlText $button $Text
}

function New-TextBox() {
    $box = New-Object System.Windows.Forms.TextBox
    $box.Dock = "Fill"
    $box.Font = New-Font 9
    return $box
}

function Add-TextColumn($Grid, [string]$Name, [string]$Header, [int]$Width) {
    $column = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
    $column.Name = $Name
    $column.Width = $Width
    [void]$Grid.Columns.Add($column)
    Register-ColumnText $column $Header | Out-Null
}

Load-Translations

$form = New-Object System.Windows.Forms.Form
$form.Size = New-Object System.Drawing.Size(1180, 760)
$form.MinimumSize = New-Object System.Drawing.Size(980, 620)
$form.StartPosition = "CenterScreen"
$form.Font = New-Font 9
Register-ControlText $form "Japanese Game Translator" | Out-Null

$main = New-Object System.Windows.Forms.TableLayoutPanel
$main.Dock = "Fill"
$main.RowCount = 4
$main.ColumnCount = 1
$main.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42))) | Out-Null
$main.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 132))) | Out-Null
$main.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$main.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 28))) | Out-Null
$form.Controls.Add($main)

$headerPanel = New-Object System.Windows.Forms.TableLayoutPanel
$headerPanel.Dock = "Fill"
$headerPanel.Padding = New-Object System.Windows.Forms.Padding(10, 6, 10, 2)
$headerPanel.RowCount = 1
$headerPanel.ColumnCount = 4
$headerPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$headerPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 74))) | Out-Null
$headerPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 146))) | Out-Null
$headerPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 1))) | Out-Null
$titleLabel = New-Label "Japanese Game Translator"
$titleLabel.Font = New-Font 10 ([System.Drawing.FontStyle]::Bold)
$headerPanel.Controls.Add($titleLabel, 0, 0)
$headerPanel.Controls.Add((New-Label "Language"), 1, 0)
$languageBox = New-Object System.Windows.Forms.ComboBox
$languageBox.Dock = "Fill"
$languageBox.DropDownStyle = "DropDownList"
[void]$languageBox.Items.Add((T "lang_zh_name"))
[void]$languageBox.Items.Add((T "lang_en_name"))
$languageBox.SelectedIndex = 0
$languageBox.Add_SelectedIndexChanged({
    if ($languageBox.SelectedIndex -eq 1) {
        $script:CurrentLanguage = "en"
    } else {
        $script:CurrentLanguage = "zh-Hans"
    }
    Apply-Localization
})
$headerPanel.Controls.Add($languageBox, 2, 0)
$main.Controls.Add($headerPanel, 0, 0)

$pathPanel = New-Object System.Windows.Forms.TableLayoutPanel
$pathPanel.Dock = "Fill"
$pathPanel.Padding = New-Object System.Windows.Forms.Padding(10, 4, 10, 8)
$pathPanel.RowCount = 3
$pathPanel.ColumnCount = 3
$pathPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 38))) | Out-Null
$pathPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 38))) | Out-Null
$pathPanel.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 38))) | Out-Null
$pathPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 96))) | Out-Null
$pathPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$pathPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 104))) | Out-Null
$main.Controls.Add($pathPanel, 0, 1)

$gameBox = New-TextBox
$workspaceBox = New-TextBox
$outputBox = New-TextBox
$pathPanel.Controls.Add((New-Label "Game Dir"), 0, 0)
$pathPanel.Controls.Add($gameBox, 1, 0)
$pathPanel.Controls.Add((New-Button "Browse" { Browse-Folder $gameBox "Choose game directory" }), 2, 0)
$pathPanel.Controls.Add((New-Label "Workspace"), 0, 1)
$pathPanel.Controls.Add($workspaceBox, 1, 1)
$pathPanel.Controls.Add((New-Button "Browse" { Browse-Folder $workspaceBox "Choose workspace" }), 2, 1)
$pathPanel.Controls.Add((New-Label "Output Dir"), 0, 2)
$pathPanel.Controls.Add($outputBox, 1, 2)
$pathPanel.Controls.Add((New-Button "Browse" { Browse-Folder $outputBox "Choose output directory" }), 2, 2)

$tabs = New-Object System.Windows.Forms.TabControl
$tabs.Dock = "Fill"
$main.Controls.Add($tabs, 0, 2)

$workflowTab = New-Object System.Windows.Forms.TabPage
Register-ControlText $workflowTab "Workflow" | Out-Null
$entriesTab = New-Object System.Windows.Forms.TabPage
Register-ControlText $entriesTab "Entries" | Out-Null
$glossaryTab = New-Object System.Windows.Forms.TabPage
Register-ControlText $glossaryTab "Glossary" | Out-Null
$toolsTab = New-Object System.Windows.Forms.TabPage
Register-ControlText $toolsTab "Open-source Tools" | Out-Null
$tabs.TabPages.AddRange(@($workflowTab, $entriesTab, $glossaryTab, $toolsTab))

$workflowLayout = New-Object System.Windows.Forms.TableLayoutPanel
$workflowLayout.Dock = "Fill"
$workflowLayout.Padding = New-Object System.Windows.Forms.Padding(8)
$workflowLayout.RowCount = 3
$workflowLayout.ColumnCount = 2
$workflowLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 136))) | Out-Null
$workflowLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 44))) | Out-Null
$workflowLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$workflowLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50))) | Out-Null
$workflowLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50))) | Out-Null
$workflowTab.Controls.Add($workflowLayout)

$actions = New-Object System.Windows.Forms.TableLayoutPanel
$actions.Dock = "Fill"
$actions.RowCount = 2
$actions.ColumnCount = 3
$actions.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 50))) | Out-Null
$actions.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 50))) | Out-Null
1..3 | ForEach-Object { $actions.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 33.33))) | Out-Null }
$workflowLayout.Controls.Add($actions, 0, 0)

$actions.Controls.Add((New-Button "Detect" {
    try {
        $game = Require-Text $gameBox (T "game_dir")
        Start-JgtTask (T "detect") @("detect", $game) { param($output) [System.Windows.Forms.MessageBox]::Show($output, (T "detect_result")) | Out-Null }
    } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "missing_path"), "OK", "Warning") | Out-Null }
}), 0, 0)
$actions.Controls.Add((New-Button "Extract" {
    try {
        $game = Require-Text $gameBox (T "game_dir")
        $workspace = Require-Text $workspaceBox (T "workspace")
        Start-JgtTask (T "extract") @("extract", $game, "--workspace", $workspace) { Load-WorkspaceData; [System.Windows.Forms.MessageBox]::Show((T "extraction_complete"), (T "done")) | Out-Null }
    } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "missing_path"), "OK", "Warning") | Out-Null }
}), 1, 0)
$actions.Controls.Add((New-Button "Load Workspace" { Load-WorkspaceData }), 2, 0)
$actions.Controls.Add((New-Button "Build Terms" {
    try {
        $workspace = Require-Text $workspaceBox (T "workspace")
        Start-JgtTask (T "build_terms") @("terms", $workspace, "--min-count", ([string]$termMinBox.Value), "--limit", ([string]$termLimitBox.Value)) { Load-WorkspaceData }
    } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "missing_path"), "OK", "Warning") | Out-Null }
}), 0, 1)
$actions.Controls.Add((New-Button "Translate Batch" {
    try {
        $workspace = Require-Text $workspaceBox (T "workspace")
        $config = Require-Text $configBox (T "config")
        Start-JgtTask (T "translate_batch") @("translate", $workspace, "--config", $config, "--provider", $providerBox.Text.Trim(), "--target", $targetBox.Text.Trim(), "--limit", ([string]$batchLimitBox.Value)) { Load-WorkspaceData }
    } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "missing_path"), "OK", "Warning") | Out-Null }
}), 1, 1)
$actions.Controls.Add((New-Button "Apply" {
    try {
        $args = @("apply", (Require-Text $gameBox (T "game_dir")), (Require-Text $workspaceBox (T "workspace")), (Require-Text $outputBox (T "output_dir")))
        if ($overwriteCheck.Checked) { $args += "--overwrite" }
        Start-JgtTask (T "apply") $args { param($output) [System.Windows.Forms.MessageBox]::Show($output, (T "done")) | Out-Null }
    } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, (T "missing_path"), "OK", "Warning") | Out-Null }
}), 2, 1)

$settings = New-Object System.Windows.Forms.TableLayoutPanel
$settings.Dock = "Fill"
$settings.RowCount = 5
$settings.ColumnCount = 3
$settings.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 78))) | Out-Null
$settings.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$settings.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 78))) | Out-Null
$workflowLayout.Controls.Add($settings, 1, 0)

$configBox = New-TextBox
$configBox.Text = Join-Path $script:RepoRoot "configs\providers.example.json"
$providerBox = New-TextBox
$providerBox.Text = "openai"
$targetBox = New-TextBox
$targetBox.Text = "zh-Hans"
$batchLimitBox = New-Object System.Windows.Forms.NumericUpDown
$batchLimitBox.Minimum = 1
$batchLimitBox.Maximum = 200
$batchLimitBox.Value = 20
$batchLimitBox.Dock = "Left"
$overwriteCheck = New-Object System.Windows.Forms.CheckBox
$overwriteCheck.Dock = "Fill"
Register-ControlText $overwriteCheck "Overwrite existing output directory" | Out-Null

$settings.Controls.Add((New-Label "Config"), 0, 0)
$settings.Controls.Add($configBox, 1, 0)
$settings.Controls.Add((New-Button "Browse" { Browse-File $configBox "Choose provider config" }), 2, 0)
$settings.Controls.Add((New-Label "Provider"), 0, 1)
$settings.Controls.Add($providerBox, 1, 1)
$settings.Controls.Add((New-Label "Target"), 0, 2)
$settings.Controls.Add($targetBox, 1, 2)
$settings.Controls.Add((New-Label "Batch"), 0, 3)
$settings.Controls.Add($batchLimitBox, 1, 3)
$settings.Controls.Add($overwriteCheck, 1, 4)

$termSettings = New-Object System.Windows.Forms.FlowLayoutPanel
$termSettings.Dock = "Fill"
$workflowLayout.SetColumnSpan($termSettings, 2)
$workflowLayout.Controls.Add($termSettings, 0, 1)
$termMinBox = New-Object System.Windows.Forms.NumericUpDown
$termMinBox.Minimum = 1
$termMinBox.Maximum = 20
$termMinBox.Value = 2
$termLimitBox = New-Object System.Windows.Forms.NumericUpDown
$termLimitBox.Minimum = 10
$termLimitBox.Maximum = 2000
$termLimitBox.Value = 200
$termSettings.Controls.Add((New-Label "Term min count"))
$termSettings.Controls.Add($termMinBox)
$termSettings.Controls.Add((New-Label "Candidate limit"))
$termSettings.Controls.Add($termLimitBox)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Dock = "Fill"
$logBox.Multiline = $true
$logBox.ScrollBars = "Vertical"
$logBox.ReadOnly = $true
$logBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$workflowLayout.SetColumnSpan($logBox, 2)
$workflowLayout.Controls.Add($logBox, 0, 2)

$entriesLayout = New-Object System.Windows.Forms.SplitContainer
$entriesLayout.Dock = "Fill"
$entriesLayout.Orientation = "Vertical"
$entriesLayout.SplitterDistance = 720
$entriesTab.Controls.Add($entriesLayout)

$entriesLeft = New-Object System.Windows.Forms.TableLayoutPanel
$entriesLeft.Dock = "Fill"
$entriesLeft.RowCount = 2
$entriesLeft.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 36))) | Out-Null
$entriesLeft.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$entriesLayout.Panel1.Controls.Add($entriesLeft)

$entryTop = New-Object System.Windows.Forms.TableLayoutPanel
$entryTop.Dock = "Fill"
$entryTop.ColumnCount = 4
$entryTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 48))) | Out-Null
$entryTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$entryTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 92))) | Out-Null
$entryTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 72))) | Out-Null
$entriesLeft.Controls.Add($entryTop, 0, 0)
$entrySearchBox = New-TextBox
$entryTop.Controls.Add((New-Label "Search"), 0, 0)
$entryTop.Controls.Add($entrySearchBox, 1, 0)
$entryTop.Controls.Add((New-Button "Save" { Save-SelectedEntry }), 2, 0)
$entryTop.Controls.Add((New-Button "Refresh" { Load-WorkspaceData }), 3, 0)
$entrySearchBox.Add_TextChanged({ Refresh-EntriesGrid })

$entriesGrid = New-Object System.Windows.Forms.DataGridView
$entriesGrid.Dock = "Fill"
$entriesGrid.AllowUserToAddRows = $false
$entriesGrid.ReadOnly = $true
$entriesGrid.SelectionMode = "FullRowSelect"
$entriesGrid.MultiSelect = $false
$entriesGrid.AutoSizeRowsMode = "None"
Add-TextColumn $entriesGrid "status" "Status" 80
Add-TextColumn $entriesGrid "source" "Source" 280
Add-TextColumn $entriesGrid "translation" "Translation" 280
Add-TextColumn $entriesGrid "file" "File" 180
$entriesLeft.Controls.Add($entriesGrid, 0, 1)

$entryDetail = New-Object System.Windows.Forms.TableLayoutPanel
$entryDetail.Dock = "Fill"
$entryDetail.Padding = New-Object System.Windows.Forms.Padding(8)
$entryDetail.RowCount = 5
$entryDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 24))) | Out-Null
$entryDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 45))) | Out-Null
$entryDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 24))) | Out-Null
$entryDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 55))) | Out-Null
$entryDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 36))) | Out-Null
$entriesLayout.Panel2.Controls.Add($entryDetail)
$entrySourceBox = New-Object System.Windows.Forms.TextBox
$entrySourceBox.Dock = "Fill"
$entrySourceBox.Multiline = $true
$entrySourceBox.ScrollBars = "Vertical"
$entrySourceBox.ReadOnly = $true
$entryTranslationBox = New-Object System.Windows.Forms.TextBox
$entryTranslationBox.Dock = "Fill"
$entryTranslationBox.Multiline = $true
$entryTranslationBox.ScrollBars = "Vertical"
$entryStatusBox = New-Object System.Windows.Forms.ComboBox
$entryStatusBox.DropDownStyle = "DropDownList"
$entryStatusBox.Items.AddRange(@("new", "translated", "reviewed", "locked", "rejected"))
$entryDetail.Controls.Add((New-Label "Source"), 0, 0)
$entryDetail.Controls.Add($entrySourceBox, 0, 1)
$entryDetail.Controls.Add((New-Label "Translation"), 0, 2)
$entryDetail.Controls.Add($entryTranslationBox, 0, 3)
$entryDetail.Controls.Add($entryStatusBox, 0, 4)
$entriesGrid.Add_SelectionChanged({
    if ($entriesGrid.SelectedRows.Count -eq 0) { return }
    $entry = Find-EntryById ([string]$entriesGrid.SelectedRows[0].Tag)
    if ($null -eq $entry) { return }
    $entrySourceBox.Text = [string]$entry.source
    $entryTranslationBox.Text = [string]$entry.translation
    $entryStatusBox.Text = [string]$entry.status
})

$glossaryLayout = New-Object System.Windows.Forms.SplitContainer
$glossaryLayout.Dock = "Fill"
$glossaryLayout.Orientation = "Vertical"
$glossaryLayout.SplitterDistance = 680
$glossaryTab.Controls.Add($glossaryLayout)

$glossaryLeft = New-Object System.Windows.Forms.TableLayoutPanel
$glossaryLeft.Dock = "Fill"
$glossaryLeft.RowCount = 2
$glossaryLeft.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 36))) | Out-Null
$glossaryLeft.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$glossaryLayout.Panel1.Controls.Add($glossaryLeft)

$termTop = New-Object System.Windows.Forms.TableLayoutPanel
$termTop.Dock = "Fill"
$termTop.ColumnCount = 4
$termTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 48))) | Out-Null
$termTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$termTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 92))) | Out-Null
$termTop.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 72))) | Out-Null
$glossaryLeft.Controls.Add($termTop, 0, 0)
$termSearchBox = New-TextBox
$termTop.Controls.Add((New-Label "Search"), 0, 0)
$termTop.Controls.Add($termSearchBox, 1, 0)
$termTop.Controls.Add((New-Button "Save" { Save-SelectedTerm }), 2, 0)
$termTop.Controls.Add((New-Button "Save All" { Save-GlossaryData }), 3, 0)
$termSearchBox.Add_TextChanged({ Refresh-GlossaryGrid })

$glossaryGrid = New-Object System.Windows.Forms.DataGridView
$glossaryGrid.Dock = "Fill"
$glossaryGrid.AllowUserToAddRows = $false
$glossaryGrid.ReadOnly = $true
$glossaryGrid.SelectionMode = "FullRowSelect"
$glossaryGrid.MultiSelect = $false
Add-TextColumn $glossaryGrid "source" "Term" 220
Add-TextColumn $glossaryGrid "target" "Term Target" 220
Add-TextColumn $glossaryGrid "status" "Status" 90
Add-TextColumn $glossaryGrid "count" "Count" 70
$glossaryLeft.Controls.Add($glossaryGrid, 0, 1)

$termDetail = New-Object System.Windows.Forms.TableLayoutPanel
$termDetail.Dock = "Fill"
$termDetail.Padding = New-Object System.Windows.Forms.Padding(8)
$termDetail.RowCount = 5
$termDetail.ColumnCount = 2
$termDetail.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 70))) | Out-Null
$termDetail.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$termDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 36))) | Out-Null
$termDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 36))) | Out-Null
$termDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 36))) | Out-Null
$termDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$termDetail.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42))) | Out-Null
$glossaryLayout.Panel2.Controls.Add($termDetail)
$termSourceBox = New-TextBox
$termSourceBox.ReadOnly = $true
$termTargetBox = New-TextBox
$termStatusBox = New-Object System.Windows.Forms.ComboBox
$termStatusBox.DropDownStyle = "DropDownList"
$termStatusBox.Items.AddRange(@("pending", "approved", "rejected"))
$termNoteBox = New-Object System.Windows.Forms.TextBox
$termNoteBox.Dock = "Fill"
$termNoteBox.Multiline = $true
$termNoteBox.ScrollBars = "Vertical"
$termButtons = New-Object System.Windows.Forms.FlowLayoutPanel
$termButtons.Dock = "Fill"
$termDetail.Controls.Add((New-Label "Term"), 0, 0)
$termDetail.Controls.Add($termSourceBox, 1, 0)
$termDetail.Controls.Add((New-Label "Term Target"), 0, 1)
$termDetail.Controls.Add($termTargetBox, 1, 1)
$termDetail.Controls.Add((New-Label "Status"), 0, 2)
$termDetail.Controls.Add($termStatusBox, 1, 2)
$termDetail.Controls.Add((New-Label "Note"), 0, 3)
$termDetail.Controls.Add($termNoteBox, 1, 3)
$termDetail.Controls.Add($termButtons, 1, 4)
$termButtons.Controls.Add((New-Button "Approve" { $termStatusBox.Text = "approved"; Save-SelectedTerm }))
$termButtons.Controls.Add((New-Button "Reject" { $termStatusBox.Text = "rejected"; Save-SelectedTerm }))
$termButtons.Controls.Add((New-Button "Pending" { $termStatusBox.Text = "pending"; Save-SelectedTerm }))
$glossaryGrid.Add_SelectionChanged({
    if ($glossaryGrid.SelectedRows.Count -eq 0) { return }
    $term = Find-TermBySource ([string]$glossaryGrid.SelectedRows[0].Tag)
    if ($null -eq $term) { return }
    $termSourceBox.Text = [string]$term.source
    $termTargetBox.Text = [string]$term.target
    $termStatusBox.Text = [string]$term.status
    $termNoteBox.Text = [string]$term.note
})

$toolsLayout = New-Object System.Windows.Forms.TableLayoutPanel
$toolsLayout.Dock = "Fill"
$toolsLayout.RowCount = 2
$toolsLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$toolsLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 42))) | Out-Null
$toolsTab.Controls.Add($toolsLayout)
$toolsGrid = New-Object System.Windows.Forms.DataGridView
$toolsGrid.Dock = "Fill"
$toolsGrid.AllowUserToAddRows = $false
$toolsGrid.ReadOnly = $true
$toolsGrid.SelectionMode = "FullRowSelect"
$toolsGrid.MultiSelect = $false
Add-TextColumn $toolsGrid "name" "Tool" 160
Add-TextColumn $toolsGrid "engines" "Engines" 380
Add-TextColumn $toolsGrid "role" "Role" 190
Add-TextColumn $toolsGrid "license" "License" 140
$toolsLayout.Controls.Add($toolsGrid, 0, 0)
$toolsButton = New-Button "Open Homepage" {
    if ($toolsGrid.SelectedRows.Count -eq 0) { return }
    $url = [string]$toolsGrid.SelectedRows[0].Tag
    if (![string]::IsNullOrWhiteSpace($url)) {
        Start-Process $url
    }
}
$toolsLayout.Controls.Add($toolsButton, 0, 1)

$statusPanel = New-Object System.Windows.Forms.TableLayoutPanel
$statusPanel.Dock = "Fill"
$statusPanel.ColumnCount = 2
$statusPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100))) | Out-Null
$statusPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 160))) | Out-Null
$statusLabel = New-Object System.Windows.Forms.Label
Register-ControlText $statusLabel "Ready" | Out-Null
$statusLabel.Dock = "Fill"
$statusLabel.TextAlign = "MiddleLeft"
$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Dock = "Fill"
$statusPanel.Controls.Add($statusLabel, 0, 0)
$statusPanel.Controls.Add($progress, 1, 0)
$main.Controls.Add($statusPanel, 0, 3)

try {
    $catalogPath = Join-Path $script:SrcRoot "jp_game_translator\resources\tool_catalog.json"
    $catalog = Get-Content -LiteralPath $catalogPath -Encoding UTF8 -Raw | ConvertFrom-Json
    foreach ($tool in $catalog.tools) {
        $rowIndex = $toolsGrid.Rows.Add($tool.name, ($tool.engines -join ", "), $tool.role, $tool.license)
        $toolsGrid.Rows[$rowIndex].Tag = $tool.url
    }
} catch {
    Append-Log ((T "tool_catalog_failed") + $_.Exception.Message)
}

[System.Windows.Forms.Application]::EnableVisualStyles()
[void]$form.ShowDialog()
