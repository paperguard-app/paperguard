<#
.SYNOPSIS
PaperGuard Lite — Safe Native Windows Installer (Antigravity, Claude Code & Cursor)
.EXAMPLE
.\install.ps1 -Project C:\Users\you\papers\neurips2026
.\install.ps1 -Global
.\install.ps1 -Target cursor -Project C:\Users\you\papers\neurips2026
#>
[CmdletBinding()]
param(
    [ValidateSet("antigravity", "claude", "cursor", "all")]
    [string]$Target = "all",
    [string]$Project = "",
    [switch]$Global
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcSkill = Join-Path $ScriptDir "paperguard-lite"

if ($Global -and $Target -eq "cursor") {
    Write-Error "Error: -Target cursor requires -Project <dir> (Cursor rules are workspace-scoped)."
    exit 1
}

if ($Global) {
    $DestRoot = $HOME
} elseif ($Project) {
    $DestRoot = (Resolve-Path $Project).Path
} else {
    $DestRoot = (Get-Location).Path
}

function Backup-IfExists([string]$TargetPath) {
    if (Test-Path -LiteralPath $TargetPath) {
        $OrigBak = "$TargetPath.orig.bak"
        $LatestBak = "$TargetPath.bak"
        if (-not (Test-Path -LiteralPath $OrigBak)) {
            Copy-Item -LiteralPath $TargetPath -Destination $OrigBak -Recurse -Force
        }
        if (Test-Path -LiteralPath $LatestBak) {
            Remove-Item -LiteralPath $LatestBak -Recurse -Force
        }
        Move-Item -LiteralPath $TargetPath -Destination $LatestBak -Force
    }
}

function Install-LiteInto([string]$SkillsDir) {
    New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null
    $ModDest = Join-Path $SkillsDir "paperguard-lite"
    Backup-IfExists $ModDest
    Copy-Item -LiteralPath $SrcSkill -Destination $ModDest -Recurse -Force
    Get-ChildItem -Path $ModDest -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Write-Host "[PaperGuard Lite] Installed -> $ModDest"
}

function Install-CursorRule([string]$RuleFile, [string]$ManagedContent) {
    $Parent = Split-Path -Parent $RuleFile
    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    $FinalText = $ManagedContent
    if (Test-Path -LiteralPath $RuleFile) {
        $Existing = Get-Content -LiteralPath $RuleFile -Raw -Encoding UTF8
        if ($Existing -ne $ManagedContent) {
            $OrigBak = "$RuleFile.orig.bak"
            $LatestBak = "$RuleFile.bak"
            if (-not (Test-Path -LiteralPath $OrigBak)) {
                Copy-Item -LiteralPath $RuleFile -Destination $OrigBak -Force
            }
            Copy-Item -LiteralPath $RuleFile -Destination $LatestBak -Force
            $Marker = "<!-- paperguard:managed-end -->"
            $UserTail = ""
            if ($Existing.Contains($Marker)) {
                $Idx = $Existing.IndexOf($Marker) + $Marker.Length
                $UserTail = $Existing.Substring($Idx).Trim()
            } else {
                $ManagedLines = $ManagedContent -split "`r?`n"
                $Extra = ($Existing -split "`r?`n") | Where-Object { $_.Trim() -ne "" -and ($ManagedLines -notcontains $_) }
                $UserTail = ($Extra -join "`n").Trim()
            }
            if ($UserTail) {
                $FinalText = $ManagedContent.TrimEnd() + "`n`n" + $UserTail + "`n"
                Write-Host "[PaperGuard Lite] Preserved local Cursor rule edits & saved backup -> $LatestBak"
            }
        }
    }
    Set-Content -LiteralPath $RuleFile -Value $FinalText -Encoding UTF8 -NoNewline
}

if ($Target -eq "antigravity" -or $Target -eq "all") {
    if ($Global) {
        Install-LiteInto (Join-Path $HOME ".gemini\config\skills")
    } else {
        Install-LiteInto (Join-Path $DestRoot ".agents\skills")
    }
}

if ($Target -eq "claude" -or $Target -eq "all") {
    Install-LiteInto (Join-Path $DestRoot ".claude\skills")
}

if (($Target -eq "cursor" -or $Target -eq "all") -and (-not $Global)) {
    if ($Target -eq "cursor") {
        Install-LiteInto (Join-Path $DestRoot ".agents\skills")
    }
    $RuleContent = @"
---
description: PaperGuard Lite read-only LaTeX/Markdown citation & style diagnostic workflow.
globs: "**/*.tex, **/*.bib, **/*.md"
alwaysApply: false
---

<!-- paperguard:managed-start -->
# PaperGuard Lite Bridge

Read `.agents/skills/paperguard-lite/SKILL.md` to run read-only citation forensics (`audit_bib_lite.py`), prose cliché diagnostics (`scan_ai_slop_lite.py`), and local HTML cockpit rendering (`render_audit_ui.py`). Never modify original manuscript files during a Lite audit.
<!-- paperguard:managed-end -->
"@ + "`n"
    $RulePath = Join-Path $DestRoot ".cursor\rules\paperguard-lite.mdc"
    Install-CursorRule $RulePath $RuleContent
    Write-Host "[PaperGuard Lite] Generated Cursor rule -> $RulePath"
}

Write-Host "Done! Double-click Start-PaperGuard-Win.bat or ask your agent to run paperguard-lite."
