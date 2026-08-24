[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:E\d+\.\d+|T\d+\.\d+[a-z])$')]
    [string]$TaskId,

    [Parameter(Mandatory = $true)]
    [string]$VerificationRoot,

    [Parameter(ParameterSetName = 'Run')]
    [string]$CommandId,

    [Parameter(ParameterSetName = 'Run')]
    [string]$FilePath,

    [Parameter(ParameterSetName = 'Run')]
    [string[]]$ArgumentList = @(),

    [Parameter(ParameterSetName = 'Run')]
    [object]$StructuredInputObject,

    [Parameter(ParameterSetName = 'SelfTest', Mandatory = $true)]
    [switch]$SelfTest,

    [string]$ManifestPath,

    [switch]$TestOnly,

    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$EvidenceId = ([Guid]::NewGuid().ToString())
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:TrustedManifestGeneration = 't13b-prequalification-20260824-05'
$script:TrustedManifestSha256 = '576d17d0708eb06cda3564376281848c97c1b5d8d91548b2e651692b9bc47bb2'
$script:CanonicalManifestPath = Join-Path `
    (Split-Path -Parent $PSScriptRoot) `
    'verification_commands.json'
$script:TestOnlyVerificationBase = 'D:\personal\obsidian_workflow\verification-artifacts'
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestPath = $script:CanonicalManifestPath
}

function Assert-NoAlternateDataStreamPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $normalizedInput = $LiteralPath.Replace('/', '\')
    if ($normalizedInput -match '^[A-Za-z]:\\') {
        if ($normalizedInput.Substring(2).Contains(':')) {
            throw "Alternate data stream paths are not allowed: $LiteralPath"
        }
    }
    elseif ($normalizedInput.Contains(':')) {
        throw "Alternate data stream paths are not allowed: $LiteralPath"
    }
    $fullPath = [System.IO.Path]::GetFullPath($LiteralPath)
    if ($fullPath -notmatch '^[A-Za-z]:\\') {
        throw "Only local drive paths are allowed: $LiteralPath"
    }
    if ($fullPath.Substring(2).Contains(':')) {
        throw "Alternate data stream paths are not allowed: $LiteralPath"
    }
}

function Get-CanonicalPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [switch]$MustExist
    )

    if ($LiteralPath -match '^(\\\\|//|\\\\\?\\|\\\\\.\\)') {
        throw "UNC and device paths are not allowed: $LiteralPath"
    }
    Assert-NoAlternateDataStreamPath -LiteralPath $LiteralPath

    if ($MustExist) {
        return (Resolve-Path -LiteralPath $LiteralPath -ErrorAction Stop).ProviderPath.TrimEnd('\')
    }

    return [System.IO.Path]::GetFullPath($LiteralPath).TrimEnd('\')
}

function Get-Sha256BytesHex {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [byte[]]$Bytes
    )

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($Bytes)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally {
        $algorithm.Dispose()
    }
}

function Get-Utf8TextAttestation {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Text
    )

    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $bytes = $utf8.GetBytes($Text)
    return [pscustomobject]@{
        byte_count = [int64]$bytes.Length
        sha256 = Get-Sha256BytesHex -Bytes $bytes
    }
}

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $stream = [System.IO.File]::Open(
        $LiteralPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite
    )
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $algorithm.ComputeHash($stream)
        return (($hash | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Get-FileAttestation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $canonical = Get-CanonicalPath -LiteralPath $LiteralPath -MustExist
    Assert-NoReparseAncestry -LiteralPath $canonical
    $item = Get-Item -LiteralPath $canonical -Force
    if ($item.PSIsContainer) {
        throw "File attestation requires a file: $canonical"
    }
    return [pscustomobject]@{
        path = $canonical
        length = [long]$item.Length
        creation_time_utc = $item.CreationTimeUtc.ToString('o')
        last_write_time_utc = $item.LastWriteTimeUtc.ToString('o')
        sha256 = Get-Sha256Hex -LiteralPath $canonical
    }
}

function Test-AttestationMatch {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Before,

        [Parameter(Mandatory = $true)]
        [object]$After
    )

    return (
        [string]$Before.path -eq [string]$After.path -and
        [long]$Before.length -eq [long]$After.length -and
        [string]$Before.creation_time_utc -eq [string]$After.creation_time_utc -and
        [string]$Before.last_write_time_utc -eq [string]$After.last_write_time_utc -and
        [string]$Before.sha256 -eq [string]$After.sha256
    )
}

function Test-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $candidatePath = (Get-CanonicalPath -LiteralPath $Candidate -MustExist)
    $rootPath = (Get-CanonicalPath -LiteralPath $Root -MustExist)
    if ($candidatePath.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }

    return $candidatePath.StartsWith(
        $rootPath + [System.IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Test-PathAtOrBelowRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $candidatePath = (Get-CanonicalPath -LiteralPath $Candidate).ToLowerInvariant()
    $rootPath = (Get-CanonicalPath -LiteralPath $Root).ToLowerInvariant()
    if ($candidatePath.Equals($rootPath, [StringComparison]::Ordinal)) {
        return $true
    }

    return $candidatePath.StartsWith(
        $rootPath + [System.IO.Path]::DirectorySeparatorChar,
        [StringComparison]::Ordinal
    )
}

function Test-AllowedMutationPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string[]]$ExactPaths,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$OutputRoots
    )

    $candidatePath = (Get-CanonicalPath -LiteralPath $Candidate).ToLowerInvariant()
    if ($ExactPaths -contains $candidatePath) {
        return $true
    }
    foreach ($root in $OutputRoots) {
        if (Test-PathAtOrBelowRoot -Candidate $candidatePath -Root $root) {
            return $true
        }
    }
    return $false
}

function Test-AllowedOutputAncestorDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$OutputRoots
    )

    if (-not (Test-Path -LiteralPath $Candidate -PathType Container)) {
        return $false
    }
    $candidatePath = (Get-CanonicalPath -LiteralPath $Candidate).ToLowerInvariant()
    foreach ($root in $OutputRoots) {
        $rootPath = (Get-CanonicalPath -LiteralPath $root).ToLowerInvariant()
        if ($rootPath.StartsWith(
            $candidatePath + [System.IO.Path]::DirectorySeparatorChar,
            [StringComparison]::Ordinal
        )) {
            return $true
        }
    }
    return $false
}

function Assert-NoReparseAncestry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    Assert-NoAlternateDataStreamPath -LiteralPath $LiteralPath
    $currentPath = [System.IO.Path]::GetFullPath($LiteralPath)
    if ($currentPath.Length -gt 3) {
        $currentPath = $currentPath.TrimEnd('\')
    }
    while (-not [string]::IsNullOrWhiteSpace($currentPath)) {
        if (-not (Test-Path -LiteralPath $currentPath)) {
            throw "Verification ancestry component does not exist: $currentPath"
        }
        $current = Get-Item -LiteralPath $currentPath -Force
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse points are not allowed in verification ancestry: $($current.FullName)"
        }
        $parentPath = Split-Path -Parent $currentPath
        if ([string]::IsNullOrWhiteSpace($parentPath) -or
            $parentPath.Equals($currentPath, [StringComparison]::OrdinalIgnoreCase)) {
            return
        }
        $currentPath = $parentPath
    }
}

function Get-PathSecurityAttestation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $canonical = Get-CanonicalPath -LiteralPath $LiteralPath -MustExist
    Assert-NoReparseAncestry -LiteralPath $LiteralPath
    $item = Get-Item -LiteralPath $canonical -Force
    $sections = [System.Security.AccessControl.AccessControlSections]::Access -bor
        [System.Security.AccessControl.AccessControlSections]::Owner -bor
        [System.Security.AccessControl.AccessControlSections]::Group
    $security = if ($item.PSIsContainer) {
        [System.IO.Directory]::GetAccessControl($canonical, $sections)
    }
    else {
        [System.IO.File]::GetAccessControl($canonical, $sections)
    }
    return [pscustomobject]@{
        sddl = $security.GetSecurityDescriptorSddlForm($sections)
        owner = $security.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        attributes = [int64]$item.Attributes
    }
}

function Assert-NoReparseOutputAncestry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Candidate,

        [Parameter(Mandatory = $true)]
        [string]$VerificationRoot
    )

    $candidatePath = Get-CanonicalPath -LiteralPath $Candidate
    $rootPath = Get-CanonicalPath -LiteralPath $VerificationRoot -MustExist
    if (-not (Test-PathAtOrBelowRoot -Candidate $candidatePath -Root $rootPath) -or
        $candidatePath.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Output path must be a child of VerificationRoot"
    }

    $current = [System.IO.Path]::GetDirectoryName($candidatePath)
    while (-not [string]::IsNullOrWhiteSpace($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in output ancestry: $($item.FullName)"
            }
        }
        if ($current.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
            return
        }
        $next = [System.IO.Path]::GetDirectoryName($current)
        if ([string]::IsNullOrWhiteSpace($next) -or
            $next.Equals($current, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $current = $next
    }
    throw "Output ancestry did not terminate at VerificationRoot"
}

function Write-CreateNewUtf8Json {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath,

        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $json = $Value | ConvertTo-Json -Depth 12
    $bytes = $utf8.GetBytes($json + [Environment]::NewLine)
    $stream = New-Object System.IO.FileStream(
        $LiteralPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
}

function Get-PathSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Roots
    )

    $snapshot = @{}
    foreach ($root in $Roots) {
        if (-not (Test-Path -LiteralPath $root)) {
            continue
        }

        Assert-NoReparseAncestry -LiteralPath $root
        $rootItem = Get-Item -LiteralPath $root -Force
        $items = @($rootItem) + @(Get-ChildItem -LiteralPath $root -Force -Recurse -ErrorAction Stop)
        foreach ($item in $items) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in snapshot roots: $($item.FullName)"
            }
            $path = $item.FullName.ToLowerInvariant()
            $length = if ($item.PSIsContainer) { -1L } else { [long]$item.Length }
            $snapshot[$path] = [pscustomobject]@{
                path = $item.FullName
                is_directory = [bool]$item.PSIsContainer
                length = $length
                last_write_utc = $item.LastWriteTimeUtc.ToString('o')
                sha256 = if ($item.PSIsContainer) {
                    $null
                }
                else {
                    Get-Sha256Hex -LiteralPath $item.FullName
                }
                security = Get-PathSecurityAttestation -LiteralPath $item.FullName
            }
        }
    }

    return $snapshot
}

function Get-SnapshotClosureSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Snapshot
    )

    $builder = New-Object System.Text.StringBuilder
    foreach ($key in @($Snapshot.Keys | Sort-Object)) {
        $entry = $Snapshot[$key]
        [void]$builder.Append([string]$key)
        [void]$builder.Append("`0")
        [void]$builder.Append(([bool]$entry.is_directory).ToString().ToLowerInvariant())
        [void]$builder.Append("`0")
        [void]$builder.Append([string]$entry.length)
        [void]$builder.Append("`0")
        [void]$builder.Append([string]$entry.sha256)
        [void]$builder.Append("`0")
        [void]$builder.Append([string]$entry.security.sddl)
        [void]$builder.Append("`0")
        [void]$builder.Append([string]$entry.security.owner)
        [void]$builder.Append("`0")
        [void]$builder.Append([string]$entry.security.attributes)
        [void]$builder.Append("`n")
    }
    return (Get-Utf8TextAttestation -Text $builder.ToString()).sha256
}

function Add-NoDeleteAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LiteralPath
    )

    $beforeSecurity = Get-PathSecurityAttestation -LiteralPath $LiteralPath
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) {
        throw 'Unable to resolve the current Windows SID'
    }

    $rights = [System.Security.AccessControl.FileSystemRights]::Delete -bor
        [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $identity.User,
        $rights,
        $inheritance,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Deny
    )
    $security = [System.IO.Directory]::GetAccessControl($LiteralPath)
    [void]$security.AddAccessRule($rule)
    [System.IO.Directory]::SetAccessControl($LiteralPath, $security)
    $afterSecurity = Get-PathSecurityAttestation -LiteralPath $LiteralPath
    return [pscustomobject]@{
        mutation = 'verification-root-acl-tighten'
        applied = [string]$beforeSecurity.sddl -cne [string]$afterSecurity.sddl
        before = $beforeSecurity
        after = $afterSecurity
    }
}

function Get-ManifestCommand {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Manifest,

        [Parameter(Mandatory = $true)]
        [string]$Id
    )

    $property = $Manifest.commands.PSObject.Properties[$Id]
    if ($null -eq $property) {
        throw "Command '$Id' is not allowlisted"
    }

    return $property.Value
}

function Assert-TaskAllowed {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Command,

        [Parameter(Mandatory = $true)]
        [string]$Id
    )

    if (@($Command.task_ids) -notcontains $Id) {
        throw "Task '$Id' is not authorized for command '$CommandId'"
    }
}

function Get-ExpectedExitCodes {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Command
    )

    $property = $Command.PSObject.Properties['expected_exit_codes']
    $values = @()
    if ($null -eq $property) {
        $values = @(0)
    }
    else {
        $values = @($property.Value)
    }
    if ($values.Count -eq 0) {
        throw "Command '$CommandId' has no expected exit code"
    }
    $codes = New-Object System.Collections.Generic.List[int]
    foreach ($value in $values) {
        $code = 0
        if (-not [int]::TryParse([string]$value, [ref]$code) -or $code -lt 0 -or $code -gt 255) {
            throw "Command '$CommandId' has an invalid expected exit code"
        }
        if (-not $codes.Contains($code)) {
            $codes.Add($code)
        }
    }
    return @($codes)
}

function Test-ExpectedBlockedTerminal {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Command,

        [Parameter(Mandatory = $true)]
        [int]$ExitCode,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Stdout,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Stderr
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    $contractProperty = $Command.PSObject.Properties['expected_blocked_terminal']
    if ($ExitCode -eq 0) {
        return [pscustomobject]@{
            valid = $true
            reasons = @()
            terminal = $null
            blockers = @()
        }
    }
    if ($null -eq $contractProperty) {
        return [pscustomobject]@{
            valid = $false
            reasons = @('EXPECTED_BLOCKED_TERMINAL_CONTRACT_MISSING')
            terminal = $null
            blockers = @()
        }
    }

    $contract = $contractProperty.Value
    $trimmed = $Stdout
    if ($trimmed.EndsWith("`r`n", [StringComparison]::Ordinal)) {
        $trimmed = $trimmed.Substring(0, $trimmed.Length - 2)
    }
    elseif ($trimmed.EndsWith("`n", [StringComparison]::Ordinal)) {
        $trimmed = $trimmed.Substring(0, $trimmed.Length - 1)
    }
    if ([string]::IsNullOrWhiteSpace($trimmed) -or
        $trimmed -match '[\r\n]' -or
        $trimmed -cne $trimmed.Trim()) {
        [void]$reasons.Add('EXPECTED_BLOCKED_STDOUT_NOT_SINGLE_LINE_JSON')
    }
    $parsed = $null
    try {
        $parsed = $trimmed | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        [void]$reasons.Add('EXPECTED_BLOCKED_STDOUT_INVALID_JSON')
    }

    $terminal = $null
    $blockers = @()
    if ($null -ne $parsed) {
        $actualKeys = @($parsed.PSObject.Properties.Name | Sort-Object)
        $expectedKeys = @($contract.exact_keys | ForEach-Object { [string]$_ } | Sort-Object)
        if (($actualKeys -join "`n") -cne ($expectedKeys -join "`n")) {
            [void]$reasons.Add('EXPECTED_BLOCKED_TERMINAL_KEYS_MISMATCH')
        }

        $terminalProperty = $parsed.PSObject.Properties['terminal']
        if ($null -eq $terminalProperty -or
            [string]$terminalProperty.Value -cne [string]$contract.terminal) {
            [void]$reasons.Add('EXPECTED_BLOCKED_TERMINAL_VALUE_MISMATCH')
        }
        else {
            $terminal = [string]$terminalProperty.Value
        }

        $blockersProperty = $parsed.PSObject.Properties['blockers']
        if ($null -eq $blockersProperty) {
            [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKERS_MISSING')
        }
        else {
            $blockers = @($blockersProperty.Value)
            if ([bool]$contract.nonempty_blockers -and $blockers.Count -eq 0) {
                [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKERS_EMPTY')
            }
            $seenBlockers = @{}
            foreach ($blocker in $blockers) {
                if ($blocker -isnot [string] -or [string]$blocker -cnotmatch '^[A-Z][A-Z0-9_]*$') {
                    [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKER_INVALID')
                    continue
                }
                if ($seenBlockers.ContainsKey([string]$blocker)) {
                    [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKER_DUPLICATE')
                }
                $seenBlockers[[string]$blocker] = $true
            }

            $requiredProperty = $contract.PSObject.Properties['required_blockers_in_order']
            $optionalProperty = $contract.PSObject.Properties['optional_leading_blockers']
            if ($null -eq $requiredProperty -or $null -eq $optionalProperty) {
                [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKER_CONTRACT_INCOMPLETE')
            }
            else {
                $requiredBlockers = @($requiredProperty.Value | ForEach-Object { [string]$_ })
                $optionalLeading = @($optionalProperty.Value | ForEach-Object { [string]$_ })
                $actualRequired = @($blockers)
                if ($blockers.Count -eq ($requiredBlockers.Count + 1) -and
                    $optionalLeading -ccontains [string]$blockers[0]) {
                    $actualRequired = @($blockers | Select-Object -Skip 1)
                }
                elseif ($blockers.Count -ne $requiredBlockers.Count) {
                    [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKER_SET_MISMATCH')
                    $actualRequired = @()
                }
                if (($actualRequired -join "`n") -cne ($requiredBlockers -join "`n")) {
                    [void]$reasons.Add('EXPECTED_BLOCKED_BLOCKER_ORDER_MISMATCH')
                }
            }
        }

        foreach ($field in @($contract.false_fields)) {
            $fieldName = [string]$field
            $fieldProperty = $parsed.PSObject.Properties[$fieldName]
            if ($null -eq $fieldProperty -or
                $fieldProperty.Value -isnot [bool] -or
                [bool]$fieldProperty.Value) {
                [void]$reasons.Add("EXPECTED_BLOCKED_FALSE_FIELD_INVALID:$fieldName")
            }
        }
    }

    $stderrPolicyProperty = $contract.PSObject.Properties['stderr_must_be_empty']
    if ($null -ne $stderrPolicyProperty -and
        [bool]$stderrPolicyProperty.Value -and
        -not [string]::IsNullOrEmpty($Stderr)) {
        [void]$reasons.Add('EXPECTED_BLOCKED_STDERR_NOT_EMPTY')
    }

    if ($null -ne $parsed) {
        $blockerJson = if ($blockers.Count -eq 0) {
            ''
        }
        else {
            '"' + ($blockers -join '","') + '"'
        }
        $canonical = '{"terminal":"' + [string]$contract.terminal +
            '","blockers":[' + $blockerJson +
            '],"child_created":false,"child_resumed":false,' +
            '"secret_materialized_in_child":false,"network_or_auth_used":false}'
        if ($trimmed -cne $canonical) {
            [void]$reasons.Add('EXPECTED_BLOCKED_STDOUT_NONCANONICAL')
        }
    }

    return [pscustomobject]@{
        valid = $reasons.Count -eq 0
        reasons = @($reasons)
        terminal = $terminal
        blockers = @($blockers)
    }
}

function Get-TopLevelCanonicalJsonKeys {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Json
    )

    $keys = New-Object System.Collections.Generic.List[string]
    $depth = 0
    $inString = $false
    $escaped = $false
    $stringDepth = 0
    $stringBuilder = $null
    for ($index = 0; $index -lt $Json.Length; $index++) {
        $character = $Json[$index]
        if ($inString) {
            if ($escaped) {
                $escaped = $false
                [void]$stringBuilder.Append($character)
            }
            elseif ($character -eq '\') {
                $escaped = $true
                [void]$stringBuilder.Append($character)
            }
            elseif ($character -eq '"') {
                $inString = $false
                if ($stringDepth -eq 1 -and
                    ($index + 1) -lt $Json.Length -and
                    $Json[$index + 1] -eq ':') {
                    $keys.Add($stringBuilder.ToString())
                }
            }
            else {
                [void]$stringBuilder.Append($character)
            }
            continue
        }

        if ([char]::IsWhiteSpace($character)) {
            throw 'JSON contains noncanonical whitespace outside a string'
        }
        if ($character -eq '"') {
            $inString = $true
            $stringDepth = $depth
            $stringBuilder = New-Object System.Text.StringBuilder
        }
        elseif ($character -eq '{' -or $character -eq '[') {
            $depth++
        }
        elseif ($character -eq '}' -or $character -eq ']') {
            $depth--
            if ($depth -lt 0) {
                throw 'JSON nesting is invalid'
            }
        }
    }
    if ($inString -or $escaped -or $depth -ne 0) {
        throw 'JSON lexical closure is invalid'
    }
    return @($keys)
}

function Test-ExactInteger {
    param([object]$Value)
    return $Value -is [int] -or $Value -is [long]
}

function Test-ExactJsonNumber {
    param([object]$Value)
    return $Value -is [int] -or
        $Value -is [long] -or
        $Value -is [double] -or
        $Value -is [decimal]
}

function Test-Sha256DigestText {
    param([object]$Value)
    return $Value -is [string] -and [string]$Value -cmatch '^sha256:[0-9a-f]{64}$'
}

function ConvertTo-FixedAsciiJsonString {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    foreach ($character in $Value.ToCharArray()) {
        $code = [int][char]$character
        if ($character -eq '"') {
            [void]$builder.Append('\"')
        }
        elseif ($character -eq '\') {
            [void]$builder.Append('\\')
        }
        elseif ($code -eq 8) {
            [void]$builder.Append('\b')
        }
        elseif ($code -eq 9) {
            [void]$builder.Append('\t')
        }
        elseif ($code -eq 10) {
            [void]$builder.Append('\n')
        }
        elseif ($code -eq 12) {
            [void]$builder.Append('\f')
        }
        elseif ($code -eq 13) {
            [void]$builder.Append('\r')
        }
        elseif ($code -lt 32 -or $code -gt 126) {
            [void]$builder.Append(('\u{0:x4}' -f $code))
        }
        else {
            [void]$builder.Append($character)
        }
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-InputRecordClosureSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Records
    )

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('[')
    for ($index = 0; $index -lt $Records.Count; $index++) {
        if ($index -gt 0) {
            [void]$builder.Append(',')
        }
        $record = $Records[$index]
        [void]$builder.Append('{"file_id":')
        [void]$builder.Append([string][long]$record.file_id)
        [void]$builder.Append(',"link_count":')
        [void]$builder.Append([string][long]$record.link_count)
        [void]$builder.Append(',"path":')
        [void]$builder.Append((ConvertTo-FixedAsciiJsonString -Value ([string]$record.path)))
        [void]$builder.Append(',"sha256":')
        [void]$builder.Append((ConvertTo-FixedAsciiJsonString -Value ([string]$record.sha256)))
        [void]$builder.Append(',"size":')
        [void]$builder.Append([string][long]$record.size)
        [void]$builder.Append(',"volume":')
        [void]$builder.Append([string][long]$record.volume)
        [void]$builder.Append('}')
    }
    [void]$builder.Append(']')
    return 'sha256:' + (Get-Utf8TextAttestation -Text $builder.ToString()).sha256
}

function Test-ExpectedSuccessTerminal {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Command,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Stdout,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Stderr,

        [Parameter(Mandatory = $true)]
        [hashtable]$BeforeSnapshot,

        [Parameter(Mandatory = $true)]
        [string]$CurrentVerificationRoot
    )

    $contractProperty = $Command.PSObject.Properties['expected_success_terminal']
    if ($null -eq $contractProperty) {
        return [pscustomobject]@{
            required = $false
            valid = $true
            reasons = @()
            terminal = $null
        }
    }
    $contract = $contractProperty.Value
    $reasons = New-Object System.Collections.Generic.List[string]
    $trimmed = $Stdout
    if ($trimmed.EndsWith("`r`n", [StringComparison]::Ordinal)) {
        $trimmed = $trimmed.Substring(0, $trimmed.Length - 2)
    }
    elseif ($trimmed.EndsWith("`n", [StringComparison]::Ordinal)) {
        $trimmed = $trimmed.Substring(0, $trimmed.Length - 1)
    }
    if ([string]::IsNullOrWhiteSpace($trimmed) -or
        $trimmed -match '[\r\n]' -or
        $trimmed -cne $trimmed.Trim()) {
        [void]$reasons.Add('EXPECTED_SUCCESS_STDOUT_NOT_SINGLE_LINE_JSON')
    }
    $lexicalKeys = @()
    try {
        $lexicalKeys = @(Get-TopLevelCanonicalJsonKeys -Json $trimmed)
    }
    catch {
        [void]$reasons.Add(
            'EXPECTED_SUCCESS_STDOUT_NONCANONICAL:' + $_.Exception.GetType().Name
        )
    }
    $parsed = $null
    try {
        $parsed = $trimmed | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        [void]$reasons.Add('EXPECTED_SUCCESS_STDOUT_INVALID_JSON')
    }
    if ($null -eq $parsed) {
        return [pscustomobject]@{
            required = $true
            valid = $false
            reasons = @($reasons)
            terminal = $null
        }
    }
    try {
        $canonicalRoundTrip = $parsed | ConvertTo-Json -Compress -Depth 100
        if ($canonicalRoundTrip -cne $trimmed) {
            [void]$reasons.Add('EXPECTED_SUCCESS_STDOUT_NONCANONICAL_ROUND_TRIP')
        }
    }
    catch {
        [void]$reasons.Add('EXPECTED_SUCCESS_STDOUT_CANONICALIZATION_FAILED')
    }

    $expectedKeys = @($contract.exact_keys | ForEach-Object { [string]$_ })
    $actualKeys = @($parsed.PSObject.Properties.Name)
    if (($actualKeys | Sort-Object) -join "`n" -cne
        (($expectedKeys | Sort-Object) -join "`n")) {
        [void]$reasons.Add('EXPECTED_SUCCESS_KEYS_MISMATCH')
    }
    if (($lexicalKeys -join "`n") -cne (($expectedKeys | Sort-Object) -join "`n")) {
        [void]$reasons.Add('EXPECTED_SUCCESS_KEYS_NOT_CANONICAL_OR_DUPLICATE')
    }
    if ([string]$parsed.schema_version -cne [string]$contract.schema_version -or
        [string]$parsed.terminal -cne [string]$contract.terminal) {
        [void]$reasons.Add('EXPECTED_SUCCESS_TERMINAL_IDENTITY_MISMATCH')
    }
    if (-not (Test-ExactInteger $parsed.threshold_percent) -or
        [int]$parsed.threshold_percent -ne [int]$contract.threshold_percent -or
        -not (Test-ExactInteger $parsed.pytest_exit_code) -or
        [int]$parsed.pytest_exit_code -ne 0) {
        [void]$reasons.Add('EXPECTED_SUCCESS_THRESHOLD_OR_PYTEST_EXIT_INVALID')
    }
    if ($parsed.data_file -ne $null -or
        $parsed.branch -isnot [bool] -or -not [bool]$parsed.branch -or
        $parsed.isolated_mode -isnot [bool] -or -not [bool]$parsed.isolated_mode -or
        $parsed.pytest_output_overflow -isnot [bool] -or [bool]$parsed.pytest_output_overflow) {
        [void]$reasons.Add('EXPECTED_SUCCESS_RUNTIME_FLAGS_INVALID')
    }
    if ($parsed.coverage_percent -isnot [double] -and
        $parsed.coverage_percent -isnot [decimal] -and
        -not (Test-ExactInteger $parsed.coverage_percent)) {
        [void]$reasons.Add('EXPECTED_SUCCESS_COVERAGE_PERCENT_INVALID')
    }
    elseif ([double]$parsed.coverage_percent -lt [double]$contract.threshold_percent -or
        [double]$parsed.coverage_percent -gt 100.0) {
        [void]$reasons.Add('EXPECTED_SUCCESS_COVERAGE_PERCENT_INVALID')
    }
    if (-not (Test-ExactInteger $parsed.pytest_output_bytes) -or
        -not (Test-ExactInteger $parsed.pytest_output_limit_bytes) -or
        [long]$parsed.pytest_output_bytes -lt 0 -or
        [long]$parsed.pytest_output_limit_bytes -ne [long]$contract.output_limit_bytes -or
        [long]$parsed.pytest_output_bytes -gt [long]$parsed.pytest_output_limit_bytes) {
        [void]$reasons.Add('EXPECTED_SUCCESS_OUTPUT_BOUND_FIELDS_INVALID')
    }
    foreach ($falseField in @($contract.false_fields)) {
        $property = $parsed.PSObject.Properties[[string]$falseField]
        if ($null -eq $property -or $property.Value -isnot [bool] -or [bool]$property.Value) {
            [void]$reasons.Add("EXPECTED_SUCCESS_FALSE_FIELD_INVALID:$falseField")
        }
    }
    $tmpPathModeProperty = $contract.PSObject.Properties['pytest_tmp_path_mode']
    if ($null -ne $tmpPathModeProperty) {
        $reportedTmpPathMode = $parsed.PSObject.Properties['pytest_tmp_path_mode']
        $reportedFailures = $parsed.PSObject.Properties['pytest_failures']
        if ($null -eq $reportedTmpPathMode -or
            [string]$reportedTmpPathMode.Value -cne [string]$tmpPathModeProperty.Value) {
            [void]$reasons.Add('EXPECTED_SUCCESS_TMP_PATH_MODE_INVALID')
        }
        if ($null -eq $reportedFailures -or
            $reportedFailures.Value -isnot [System.Array] -or
            @($reportedFailures.Value).Count -ne 0) {
            [void]$reasons.Add('EXPECTED_SUCCESS_PYTEST_FAILURES_NOT_EMPTY')
        }
    }

    $expectedModules = @($contract.modules | ForEach-Object { [string]$_ })
    $actualModules = @($parsed.modules | ForEach-Object { [string]$_ })
    $expectedTests = @($contract.tests | ForEach-Object { [string]$_ })
    $actualTests = @($parsed.tests | ForEach-Object { [string]$_ })
    if (($actualModules -join "`n") -cne ($expectedModules -join "`n") -or
        ($actualTests -join "`n") -cne ($expectedTests -join "`n")) {
        [void]$reasons.Add('EXPECTED_SUCCESS_FIXED_INPUT_LIST_MISMATCH')
    }

    $statementTotal = 0L
    $missingStatementTotal = 0L
    $branchTotal = 0L
    $missingBranchTotal = 0L
    $moduleResults = @($parsed.module_results)
    if ($moduleResults.Count -ne $expectedModules.Count) {
        [void]$reasons.Add('EXPECTED_SUCCESS_MODULE_RESULT_COUNT_MISMATCH')
    }
    else {
        for ($index = 0; $index -lt $moduleResults.Count; $index++) {
            $result = $moduleResults[$index]
            $resultKeys = @($result.PSObject.Properties.Name)
            $fixedResultKeys = @(
                'branch_pct', 'branches', 'line_pct', 'missing_branches',
                'missing_statements', 'module', 'passed', 'statements'
            ) | Sort-Object
            if (($resultKeys -join "`n") -cne ($fixedResultKeys -join "`n") -or
                [string]$result.module -cne $expectedModules[$index]) {
                [void]$reasons.Add('EXPECTED_SUCCESS_MODULE_RESULT_SCHEMA_MISMATCH')
                continue
            }
            if (-not (Test-ExactInteger $result.statements) -or
                -not (Test-ExactInteger $result.missing_statements) -or
                -not (Test-ExactInteger $result.branches) -or
                -not (Test-ExactInteger $result.missing_branches) -or
                -not (Test-ExactJsonNumber $result.line_pct) -or
                -not (Test-ExactJsonNumber $result.branch_pct)) {
                [void]$reasons.Add('EXPECTED_SUCCESS_MODULE_COUNTS_NOT_INTEGERS')
                continue
            }
            $statements = [long]$result.statements
            $missingStatements = [long]$result.missing_statements
            $branches = [long]$result.branches
            $missingBranches = [long]$result.missing_branches
            if ($statements -le 0 -or $branches -le 0 -or
                $missingStatements -lt 0 -or $missingStatements -gt $statements -or
                $missingBranches -lt 0 -or $missingBranches -gt $branches -or
                $result.passed -isnot [bool] -or -not [bool]$result.passed -or
                (($statements - $missingStatements) * 100) -lt (90 * $statements) -or
                (($branches - $missingBranches) * 100) -lt (90 * $branches)) {
                [void]$reasons.Add('EXPECTED_SUCCESS_MODULE_COVERAGE_INVALID')
            }
            $expectedLinePercent = [Math]::Round(
                (($statements - $missingStatements) * 100.0 / $statements),
                2,
                [MidpointRounding]::ToEven
            )
            $expectedBranchPercent = [Math]::Round(
                (($branches - $missingBranches) * 100.0 / $branches),
                2,
                [MidpointRounding]::ToEven
            )
            if ([Math]::Abs([double]$result.line_pct - $expectedLinePercent) -gt 0.000001 -or
                [Math]::Abs([double]$result.branch_pct - $expectedBranchPercent) -gt 0.000001) {
                [void]$reasons.Add('EXPECTED_SUCCESS_MODULE_PERCENTAGE_MISMATCH')
            }
            $statementTotal += $statements
            $missingStatementTotal += $missingStatements
            $branchTotal += $branches
            $missingBranchTotal += $missingBranches
        }
    }

    $totals = $parsed.totals
    $totalKeys = @($totals.PSObject.Properties.Name)
    $fixedTotalKeys = @(
        'branch_pct', 'branches', 'line_pct', 'missing_branches',
        'missing_statements', 'statements'
    ) | Sort-Object
    if (($totalKeys -join "`n") -cne ($fixedTotalKeys -join "`n") -or
        -not (Test-ExactInteger $totals.statements) -or
        -not (Test-ExactInteger $totals.missing_statements) -or
        -not (Test-ExactInteger $totals.branches) -or
        -not (Test-ExactInteger $totals.missing_branches) -or
        -not (Test-ExactJsonNumber $totals.line_pct) -or
        -not (Test-ExactJsonNumber $totals.branch_pct) -or
        [long]$totals.statements -ne $statementTotal -or
        [long]$totals.missing_statements -ne $missingStatementTotal -or
        [long]$totals.branches -ne $branchTotal -or
        [long]$totals.missing_branches -ne $missingBranchTotal) {
        [void]$reasons.Add('EXPECTED_SUCCESS_TOTALS_MISMATCH')
    }
    if ($statementTotal -gt 0 -and $branchTotal -gt 0) {
        $expectedTotalLinePercent = [Math]::Round(
            (($statementTotal - $missingStatementTotal) * 100.0 / $statementTotal),
            2,
            [MidpointRounding]::ToEven
        )
        $expectedTotalBranchPercent = [Math]::Round(
            (($branchTotal - $missingBranchTotal) * 100.0 / $branchTotal),
            2,
            [MidpointRounding]::ToEven
        )
        $combinedDenominator = $statementTotal + $branchTotal
        $combinedMissing = $missingStatementTotal + $missingBranchTotal
        $expectedCoveragePercent = [Math]::Round(
            (($combinedDenominator - $combinedMissing) * 100.0 / $combinedDenominator),
            2,
            [MidpointRounding]::ToEven
        )
        if ([Math]::Abs([double]$totals.line_pct - $expectedTotalLinePercent) -gt 0.000001 -or
            [Math]::Abs([double]$totals.branch_pct - $expectedTotalBranchPercent) -gt 0.000001 -or
            [Math]::Abs([double]$parsed.coverage_percent - $expectedCoveragePercent) -gt 0.000001) {
            [void]$reasons.Add('EXPECTED_SUCCESS_TOTAL_PERCENTAGE_MISMATCH')
        }
    }

    foreach ($digestField in @(
        'input_closure_sha256', 'pytest_output_sha256',
        'pytest_stdout_sha256', 'pytest_stderr_sha256'
    )) {
        $property = $parsed.PSObject.Properties[$digestField]
        if ($null -eq $property -or -not (Test-Sha256DigestText $property.Value)) {
            [void]$reasons.Add("EXPECTED_SUCCESS_DIGEST_INVALID:$digestField")
        }
    }
    $expectedInputClosureProperty = $contract.PSObject.Properties['input_closure_sha256']
    if ($null -eq $expectedInputClosureProperty -or
        -not (Test-Sha256DigestText $expectedInputClosureProperty.Value)) {
        [void]$reasons.Add('EXPECTED_SUCCESS_CONTRACT_INPUT_CLOSURE_MISSING_OR_INVALID')
    }
    elseif ([string]$parsed.input_closure_sha256 -cne
        [string]$expectedInputClosureProperty.Value) {
        [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_CLOSURE_MISMATCH')
    }

    $inputRecords = @($parsed.input_records)
    $seenInputPaths = @{}
    foreach ($record in $inputRecords) {
        $recordKeys = @($record.PSObject.Properties.Name)
        $expectedRecordKeys = @('file_id', 'link_count', 'path', 'sha256', 'size', 'volume')
        if (($recordKeys -join "`n") -cne (($expectedRecordKeys | Sort-Object) -join "`n") -or
            -not (Test-ExactInteger $record.file_id) -or
            -not (Test-ExactInteger $record.link_count) -or
            -not (Test-ExactInteger $record.volume) -or
            [long]$record.link_count -le 0) {
            [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_RECORD_SCHEMA_INVALID')
        }
        $path = [string]$record.path
        $lowerPath = $path.ToLowerInvariant()
        if ($seenInputPaths.ContainsKey($lowerPath)) {
            [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_RECORD_DUPLICATE')
            continue
        }
        $seenInputPaths[$lowerPath] = $true
        if (-not $BeforeSnapshot.ContainsKey($lowerPath) -or
            [bool]$BeforeSnapshot[$lowerPath].is_directory -or
            -not (Test-ExactInteger $record.size) -or
            [long]$record.size -ne [long]$BeforeSnapshot[$lowerPath].length -or
            -not (Test-Sha256DigestText $record.sha256) -or
            [string]$record.sha256 -cne ('sha256:' + [string]$BeforeSnapshot[$lowerPath].sha256)) {
            [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_RECORD_NOT_IN_SNAPSHOT')
        }
    }
    foreach ($requiredInput in @($contract.required_input_paths)) {
        $requiredCanonical = (Get-CanonicalPath -LiteralPath ([string]$requiredInput) -MustExist).ToLowerInvariant()
        if (-not $seenInputPaths.ContainsKey($requiredCanonical)) {
            [void]$reasons.Add('EXPECTED_SUCCESS_REQUIRED_INPUT_MISSING')
        }
    }
    $expectedInputPaths = New-Object System.Collections.Generic.List[string]
    foreach ($requiredInput in @($contract.required_input_paths)) {
        $expectedInputPaths.Add(
            (Get-CanonicalPath -LiteralPath ([string]$requiredInput) -MustExist).ToLowerInvariant()
        )
    }
    $inputRootsProperty = $contract.PSObject.Properties['input_python_roots']
    if ($null -ne $inputRootsProperty) {
        foreach ($inputRoot in @($inputRootsProperty.Value)) {
            $canonicalInputRoot = Get-CanonicalPath -LiteralPath ([string]$inputRoot) -MustExist
            Assert-NoReparseAncestry -LiteralPath $canonicalInputRoot
            foreach ($inputFile in Get-ChildItem `
                -LiteralPath $canonicalInputRoot `
                -Filter '*.py' `
                -File `
                -Recurse `
                -ErrorAction Stop) {
                Assert-NoReparseAncestry -LiteralPath $inputFile.FullName
                $expectedInputPaths.Add($inputFile.FullName.ToLowerInvariant())
            }
        }
    }
    $expectedInputSet = @($expectedInputPaths | Sort-Object -Unique)
    $actualInputOrder = @($inputRecords | ForEach-Object { ([string]$_.path).ToLowerInvariant() })
    $actualInputSet = @($actualInputOrder | Sort-Object -Unique)
    if (($actualInputSet -join "`n") -cne ($expectedInputSet -join "`n") -or
        ($actualInputOrder -join "`n") -cne ($expectedInputSet -join "`n")) {
        [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_PATH_SET_OR_ORDER_MISMATCH')
    }
    try {
        $recomputedInputClosure = Get-InputRecordClosureSha256 -Records $inputRecords
        if ([string]$parsed.input_closure_sha256 -cne $recomputedInputClosure) {
            [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_CLOSURE_RECOMPUTE_MISMATCH')
        }
    }
    catch {
        [void]$reasons.Add('EXPECTED_SUCCESS_INPUT_CLOSURE_RECOMPUTE_FAILED')
    }
    $runtimeKeys = @($parsed.runtime_identities.PSObject.Properties.Name)
    $expectedRuntimeKeys = @($contract.runtime_identity_keys | ForEach-Object { [string]$_ } | Sort-Object)
    if (($runtimeKeys -join "`n") -cne ($expectedRuntimeKeys -join "`n")) {
        [void]$reasons.Add('EXPECTED_SUCCESS_RUNTIME_IDENTITIES_MISMATCH')
    }
    $relativeRuntimePathsProperty = $contract.PSObject.Properties[
        'runtime_identity_relative_paths'
    ]
    $relativeRuntimePaths = if ($null -eq $relativeRuntimePathsProperty) {
        $null
    }
    else {
        $relativeRuntimePathsProperty.Value
    }
    $runtimePathKeys = @($contract.runtime_identity_path_keys | ForEach-Object { [string]$_ })
    $mappedRuntimePathKeys = if ($null -eq $relativeRuntimePaths) {
        @()
    }
    else {
        @($relativeRuntimePaths.PSObject.Properties.Name)
    }
    $runtimeRootKeyProperty = $contract.PSObject.Properties['runtime_identity_root_key']
    $runtimeRootKey = if ($null -eq $runtimeRootKeyProperty) {
        ''
    }
    else {
        [string]$runtimeRootKeyProperty.Value
    }
    $expectedRuntimePathKeys = @($mappedRuntimePathKeys + @($runtimeRootKey) | Sort-Object)
    if ([string]::IsNullOrWhiteSpace($runtimeRootKey) -or
        (($runtimePathKeys | Sort-Object) -join "`n") -cne
        ($expectedRuntimePathKeys -join "`n")) {
        [void]$reasons.Add('EXPECTED_SUCCESS_CONTRACT_RUNTIME_PATH_MAP_INVALID')
    }
    try {
        $coverageRootTemplateProperty = $contract.PSObject.Properties['coverage_root_template']
        $runtimeRootLeafRegexProperty = $contract.PSObject.Properties['runtime_root_leaf_regex']
        if ($null -eq $coverageRootTemplateProperty -or
            $null -eq $runtimeRootLeafRegexProperty -or
            $null -eq $relativeRuntimePaths) {
            throw 'runtime identity relative-path contract is incomplete'
        }
        $expectedCoverageRoot = Get-CanonicalPath -LiteralPath ((
            [string]$coverageRootTemplateProperty.Value
        ).Replace('{verification_root}', $CurrentVerificationRoot)) -MustExist
        $reportedCoverageRoot = Get-CanonicalPath `
            -LiteralPath ([string]$parsed.coverage_root) `
            -MustExist
        if (-not $reportedCoverageRoot.Equals(
            $expectedCoverageRoot,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw 'reported coverage root does not match the fixed contract root'
        }
        $runtimeRootProperty = $parsed.runtime_identities.PSObject.Properties[$runtimeRootKey]
        if ($null -eq $runtimeRootProperty) {
            throw 'runtime root identity is missing'
        }
        $runtimeRoot = Get-CanonicalPath `
            -LiteralPath ([string]$runtimeRootProperty.Value) `
            -MustExist
        Assert-NoReparseAncestry -LiteralPath $runtimeRoot
        if (-not (Split-Path -Parent $runtimeRoot).Equals(
            $expectedCoverageRoot,
            [StringComparison]::OrdinalIgnoreCase
        ) -or -not [regex]::IsMatch(
            (Split-Path -Leaf $runtimeRoot),
            [string]$runtimeRootLeafRegexProperty.Value,
            [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
        )) {
            throw 'runtime root is not a fixed-shape direct child of the coverage root'
        }
        foreach ($runtimePathProperty in $relativeRuntimePaths.PSObject.Properties) {
            $runtimePathKey = [string]$runtimePathProperty.Name
            $relativePath = [string]$runtimePathProperty.Value
            if ([string]::IsNullOrWhiteSpace($relativePath) -or
                [System.IO.Path]::IsPathRooted($relativePath) -or
                @($relativePath.Split('\') | Where-Object { $_ -in @('', '.', '..') }).Count -ne 0) {
                throw 'runtime identity relative path is unsafe'
            }
            $runtimeProperty = $parsed.runtime_identities.PSObject.Properties[$runtimePathKey]
            if ($null -eq $runtimeProperty) {
                throw 'runtime identity path is missing'
            }
            $runtimePath = Get-CanonicalPath `
                -LiteralPath ([string]$runtimeProperty.Value) `
                -MustExist
            $expectedRuntimePath = Get-CanonicalPath `
                -LiteralPath (Join-Path $runtimeRoot $relativePath) `
                -MustExist
            Assert-NoReparseAncestry -LiteralPath $runtimePath
            if (-not $runtimePath.Equals(
                $expectedRuntimePath,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                throw 'runtime identity path does not match the fixed relative path'
            }
        }
    }
    catch {
        [void]$reasons.Add('EXPECTED_SUCCESS_RUNTIME_IDENTITY_PATH_INVALID')
    }
    $runtimeClosureProperty = $parsed.runtime_identities.PSObject.Properties[
        [string]$contract.runtime_closure_digest_key
    ]
    $runtimeCountProperty = $parsed.runtime_identities.PSObject.Properties[
        [string]$contract.runtime_closure_count_key
    ]
    if ($null -eq $runtimeClosureProperty -or
        -not (Test-Sha256DigestText $runtimeClosureProperty.Value) -or
        $null -eq $runtimeCountProperty -or
        -not (Test-ExactInteger $runtimeCountProperty.Value) -or
        [long]$runtimeCountProperty.Value -le 0) {
        [void]$reasons.Add('EXPECTED_SUCCESS_RUNTIME_CLOSURE_INVALID')
    }
    $expectedRuntimeDigestProperty = $contract.PSObject.Properties['runtime_closure_sha256']
    $expectedRuntimeCountProperty = $contract.PSObject.Properties['runtime_closure_file_count']
    if ($null -eq $expectedRuntimeDigestProperty -or
        -not (Test-Sha256DigestText $expectedRuntimeDigestProperty.Value)) {
        [void]$reasons.Add('EXPECTED_SUCCESS_CONTRACT_RUNTIME_CLOSURE_MISSING_OR_INVALID')
    }
    elseif ($null -eq $runtimeClosureProperty -or
        [string]$runtimeClosureProperty.Value -cne
        [string]$expectedRuntimeDigestProperty.Value) {
        [void]$reasons.Add('EXPECTED_SUCCESS_RUNTIME_CLOSURE_MISMATCH')
    }
    if ($null -eq $expectedRuntimeCountProperty -or
        -not (Test-ExactInteger $expectedRuntimeCountProperty.Value) -or
        [long]$expectedRuntimeCountProperty.Value -le 0) {
        [void]$reasons.Add('EXPECTED_SUCCESS_CONTRACT_RUNTIME_COUNT_MISSING_OR_INVALID')
    }
    elseif ($null -eq $runtimeCountProperty -or
        [long]$runtimeCountProperty.Value -ne [long]$expectedRuntimeCountProperty.Value) {
        [void]$reasons.Add('EXPECTED_SUCCESS_RUNTIME_CLOSURE_COUNT_MISMATCH')
    }
    try {
        $coverageRoot = Get-CanonicalPath -LiteralPath ([string]$parsed.coverage_root) -MustExist
        $reservationRoot = Get-CanonicalPath -LiteralPath ([string]$parsed.pytest_reservation) -MustExist
        $baseTempCreatedProperty = $parsed.PSObject.Properties['pytest_basetemp_created']
        $baseTempCreated = $null -eq $baseTempCreatedProperty -or
            [bool]$baseTempCreatedProperty.Value
        $baseTemp = if ($baseTempCreated) {
            Get-CanonicalPath -LiteralPath ([string]$parsed.pytest_basetemp) -MustExist
        }
        else {
            Get-CanonicalPath -LiteralPath ([string]$parsed.pytest_basetemp)
        }
        Assert-NoReparseAncestry -LiteralPath $coverageRoot
        Assert-NoReparseAncestry -LiteralPath $reservationRoot
        if ($baseTempCreated) {
            Assert-NoReparseAncestry -LiteralPath $baseTemp
        }
        else {
            if (Test-Path -LiteralPath $baseTemp) {
                throw 'pytest basetemp was reported absent but exists'
            }
            Assert-NoReparseOutputAncestry `
                -Candidate $baseTemp `
                -VerificationRoot $CurrentVerificationRoot
        }
        if ((Split-Path -Parent $reservationRoot) -cne $coverageRoot -or
            (Split-Path -Parent $baseTemp) -cne $reservationRoot -or
            -not [regex]::IsMatch(
                (Split-Path -Leaf $reservationRoot),
                [string]$contract.pytest_reservation_leaf_regex,
                [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
            ) -or
            (Split-Path -Leaf $baseTemp) -cne [string]$contract.pytest_basetemp_leaf) {
            throw 'coverage output roots are not in their fixed parent chain'
        }
        $pycacheProperty = $parsed.PSObject.Properties['pycache_root']
        if ($null -ne $pycacheProperty) {
            $pycacheRoot = Get-CanonicalPath -LiteralPath ([string]$pycacheProperty.Value) -MustExist
            Assert-NoReparseAncestry -LiteralPath $pycacheRoot
            if ((Split-Path -Parent $pycacheRoot) -cne $coverageRoot -or
                -not [regex]::IsMatch(
                    (Split-Path -Leaf $pycacheRoot),
                    [string]$contract.pycache_root_leaf_regex,
                    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
                ) -or
                @(Get-ChildItem -LiteralPath $pycacheRoot -Force).Count -ne 0) {
                throw 'pycache root is outside the output root or is not empty'
            }
        }
    }
    catch {
        [void]$reasons.Add('EXPECTED_SUCCESS_OUTPUT_PATH_CHAIN_INVALID')
    }
    if (-not [string]::IsNullOrEmpty($Stderr)) {
        [void]$reasons.Add('EXPECTED_SUCCESS_STDERR_NOT_EMPTY')
    }

    return [pscustomobject]@{
        required = $true
        valid = $reasons.Count -eq 0
        reasons = @($reasons)
        terminal = [string]$parsed.terminal
    }
}

function Get-DeclaredOutputRoots {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Command,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$CurrentVerificationRoot
    )

    $roots = New-Object System.Collections.Generic.List[string]
    $argumentSchema = @($Command.argv_schema)
    if ($Arguments.Count -ne $argumentSchema.Count) {
        throw "Argument count mismatch for '$CommandId'"
    }
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $pathProperty = $argumentSchema[$index].PSObject.Properties['verification_path']
        if ($null -eq $pathProperty -or -not [bool]$pathProperty.Value) {
            continue
        }
        $candidate = Get-CanonicalPath -LiteralPath $Arguments[$index]
        if (-not (Test-PathAtOrBelowRoot -Candidate $candidate -Root $CurrentVerificationRoot) -or
            $candidate.Equals($CurrentVerificationRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Argument $index must be a new child path under VerificationRoot"
        }
        if (Test-Path -LiteralPath $candidate) {
            throw "Argument $index requested an existing output path: $candidate"
        }
        Assert-NoReparseOutputAncestry `
            -Candidate $candidate `
            -VerificationRoot $CurrentVerificationRoot
        $roots.Add($candidate)
    }

    $createDirectoriesProperty = $Command.PSObject.Properties['create_directories']
    if ($null -ne $createDirectoriesProperty) {
        foreach ($template in @($createDirectoriesProperty.Value)) {
            $candidate = Get-CanonicalPath -LiteralPath (([string]$template).Replace(
                '{verification_root}',
                $CurrentVerificationRoot
            ))
            if (-not (Test-PathAtOrBelowRoot -Candidate $candidate -Root $CurrentVerificationRoot) -or
                $candidate.Equals($CurrentVerificationRoot, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Command '$CommandId' requested a directory outside VerificationRoot"
            }
            if (Test-Path -LiteralPath $candidate) {
                throw "Command '$CommandId' requested an existing directory: $candidate"
            }
            Assert-NoReparseOutputAncestry `
                -Candidate $candidate `
                -VerificationRoot $CurrentVerificationRoot
            $roots.Add($candidate)
        }
    }

    return @($roots | Sort-Object -Unique)
}

function Invoke-AllowlistedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Command,

        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$CurrentVerificationRoot,

        [object]$StructuredInput
    )

    if ([bool]$Command.internal) {
        throw "Internal command '$CommandId' cannot launch an external process"
    }
    if ([string]::IsNullOrWhiteSpace($Executable)) {
        throw 'FilePath is required'
    }

    Assert-NoReparseAncestry -LiteralPath $Executable
    $resolvedExecutable = Get-CanonicalPath -LiteralPath $Executable -MustExist
    if (-not $resolvedExecutable.Equals(
        [string]$Command.executable_path,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Executable path is not allowlisted for '$CommandId'"
    }

    $executableBefore = Get-FileAttestation -LiteralPath $resolvedExecutable
    if (-not ([string]$executableBefore.sha256).Equals(
        [string]$Command.executable_sha256,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Executable hash mismatch for '$CommandId'"
    }

    $argumentSchema = @($Command.argv_schema)
    if ($Arguments.Count -ne $argumentSchema.Count) {
        throw "Argument count mismatch for '$CommandId'"
    }
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $rule = $argumentSchema[$index]
        $literalProperty = $rule.PSObject.Properties['literal']
        $pathProperty = $rule.PSObject.Properties['verification_path']
        if ($null -ne $literalProperty) {
            if (-not $Arguments[$index].Equals(
                [string]$literalProperty.Value,
                [StringComparison]::Ordinal
            )) {
                throw "Argument $index is not allowlisted for '$CommandId'"
            }
        }
        elseif ($null -ne $pathProperty -and [bool]$pathProperty.Value) {
            $argumentPath = Get-CanonicalPath -LiteralPath $Arguments[$index]
            $verificationPrefix = $CurrentVerificationRoot +
                [System.IO.Path]::DirectorySeparatorChar
            if (-not $argumentPath.StartsWith(
                $verificationPrefix,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Argument $index must be a new path under VerificationRoot"
            }
        }
        else {
            throw "Unsupported argv schema rule at index $index for '$CommandId'"
        }
    }

    function ConvertTo-WindowsCommandLineArgument {
        param(
            [AllowEmptyString()]
            [string]$Value
        )

        if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
            return $Value
        }

        $builder = New-Object System.Text.StringBuilder
        [void]$builder.Append('"')
        $backslashes = 0
        foreach ($character in $Value.ToCharArray()) {
            if ($character -eq '\') {
                $backslashes++
                continue
            }
            if ($character -eq '"') {
                [void]$builder.Append(('\' * (($backslashes * 2) + 1)))
                [void]$builder.Append('"')
                $backslashes = 0
                continue
            }
            if ($backslashes -gt 0) {
                [void]$builder.Append(('\' * $backslashes))
                $backslashes = 0
            }
            [void]$builder.Append($character)
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(('\' * ($backslashes * 2)))
        }
        [void]$builder.Append('"')
        return $builder.ToString()
    }

    $processInfo = New-Object System.Diagnostics.ProcessStartInfo
    $processInfo.FileName = $resolvedExecutable
    $processInfo.UseShellExecute = $false
    $processInfo.CreateNoWindow = $true
    $processInfo.RedirectStandardOutput = $true
    $processInfo.RedirectStandardError = $true
    $processInfo.RedirectStandardInput = $null -ne $StructuredInput
    $workingDirectoryProperty = $Command.PSObject.Properties['working_directory']
    if ($null -ne $workingDirectoryProperty) {
        Assert-NoReparseAncestry -LiteralPath ([string]$workingDirectoryProperty.Value)
        $processInfo.WorkingDirectory = Get-CanonicalPath `
            -LiteralPath ([string]$workingDirectoryProperty.Value) `
            -MustExist
    }
    $environmentProperty = $Command.PSObject.Properties['environment']
    if ($null -ne $environmentProperty) {
        foreach ($property in $environmentProperty.Value.PSObject.Properties) {
            $value = ([string]$property.Value).Replace(
                '{verification_root}',
                $CurrentVerificationRoot
            )
            $processInfo.EnvironmentVariables[$property.Name] = $value
        }
    }
    $createDirectoriesProperty = $Command.PSObject.Properties['create_directories']
    if ($null -ne $createDirectoriesProperty) {
        foreach ($template in @($createDirectoriesProperty.Value)) {
            $directoryPath = ([string]$template).Replace(
                '{verification_root}',
                $CurrentVerificationRoot
            )
            $canonicalDirectory = Get-CanonicalPath -LiteralPath $directoryPath
            $verificationPrefix = $CurrentVerificationRoot +
                [System.IO.Path]::DirectorySeparatorChar
            if (-not $canonicalDirectory.StartsWith(
                $verificationPrefix,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                throw "Command '$CommandId' requested a directory outside VerificationRoot"
            }
            if (Test-Path -LiteralPath $canonicalDirectory) {
                throw "Command '$CommandId' requested an existing directory: $canonicalDirectory"
            }
            [void][System.IO.Directory]::CreateDirectory($canonicalDirectory)
            Assert-NoReparseAncestry -LiteralPath $canonicalDirectory
            if (-not (Test-PathWithinRoot `
                -Candidate $canonicalDirectory `
                -Root $CurrentVerificationRoot)) {
                throw "Command '$CommandId' created a directory outside VerificationRoot"
            }
        }
    }

    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $pathProperty = $argumentSchema[$index].PSObject.Properties['verification_path']
        if ($null -ne $pathProperty -and [bool]$pathProperty.Value) {
            $outputCandidate = Get-CanonicalPath -LiteralPath $Arguments[$index]
            if (Test-Path -LiteralPath $outputCandidate) {
                throw "Argument $index output path appeared before process start: $outputCandidate"
            }
            Assert-NoReparseOutputAncestry `
                -Candidate $outputCandidate `
                -VerificationRoot $CurrentVerificationRoot
        }
    }
    $processInfo.Arguments = (@(
        $Arguments | ForEach-Object {
            ConvertTo-WindowsCommandLineArgument -Value $_
        }
    ) -join ' ')

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $processInfo
    if (-not $process.Start()) {
        throw "Failed to start allowlisted command '$CommandId'"
    }

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    if ($null -ne $StructuredInput) {
        if ($null -eq $Command.stdin_schema) {
            $process.Kill()
            throw "Structured input is not allowlisted for '$CommandId'"
        }
        $inputJson = $StructuredInput | ConvertTo-Json -Depth 12 -Compress
        $inputBytes = [System.Text.Encoding]::UTF8.GetByteCount($inputJson)
        if ($inputBytes -gt [int]$Command.stdin_max_bytes) {
            $process.Kill()
            throw "Structured input exceeds the limit for '$CommandId'"
        }
        $process.StandardInput.Write($inputJson)
        $process.StandardInput.Close()
    }

    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $executableAfter = Get-FileAttestation -LiteralPath $resolvedExecutable
    return [pscustomobject]@{
        exit_code = $process.ExitCode
        stdout = $stdout
        stderr = $stderr
        executable_before = $executableBefore
        executable_after = $executableAfter
        executable_pre_post_match = Test-AttestationMatch `
            -Before $executableBefore `
            -After $executableAfter
    }
}

Assert-NoReparseAncestry -LiteralPath $ManifestPath
$manifestCanonical = Get-CanonicalPath -LiteralPath $ManifestPath -MustExist
$canonicalTrustedManifest = Get-CanonicalPath `
    -LiteralPath $script:CanonicalManifestPath `
    -MustExist
if (-not $TestOnly -and -not $manifestCanonical.Equals(
    $canonicalTrustedManifest,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Normal mode requires the exact repository canonical manifest: $canonicalTrustedManifest"
}

$manifestBytes = [System.IO.File]::ReadAllBytes($manifestCanonical)
$manifestPreSha256 = Get-Sha256BytesHex -Bytes $manifestBytes
$manifestBefore = Get-FileAttestation -LiteralPath $manifestCanonical
if ([string]$manifestBefore.sha256 -cne $manifestPreSha256) {
    throw 'Manifest changed while its initial bytes were being captured'
}
$strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
$manifestText = $strictUtf8.GetString($manifestBytes)
$manifest = $manifestText | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1) {
    throw "Unsupported verification manifest schema: $($manifest.schema_version)"
}
$declaredHarnessMutations = @(
    $manifest.harness_mutation_policy | ForEach-Object { [string]$_ } | Sort-Object
)
$requiredHarnessMutations = @(
    'verification-create-only',
    'verification-root-acl-tighten'
) | Sort-Object
if (($declaredHarnessMutations -join "`n") -cne ($requiredHarnessMutations -join "`n")) {
    throw 'Manifest does not declare the exact harness mutation policy'
}
if ($TestOnly) {
    if ([string]$manifest.authorization -cne 'test-only') {
        throw 'TestOnly mode requires a manifest with authorization=test-only'
    }
    $trustMode = 'HARNESS_TEST_ONLY'
}
else {
    if ([string]$manifest.generation -cne $script:TrustedManifestGeneration) {
        throw "Canonical manifest generation mismatch: $($manifest.generation)"
    }
    if ($manifestPreSha256 -cne $script:TrustedManifestSha256) {
        throw "Canonical manifest SHA-256 mismatch: $manifestPreSha256"
    }
    $trustMode = 'CANONICAL_MANIFEST'
}

Assert-NoReparseAncestry -LiteralPath $VerificationRoot
$verificationCanonical = Get-CanonicalPath -LiteralPath $VerificationRoot -MustExist
if ($TestOnly -and -not (Test-PathWithinRoot `
    -Candidate $verificationCanonical `
    -Root $script:TestOnlyVerificationBase)) {
    throw "TestOnly VerificationRoot must remain under $($script:TestOnlyVerificationBase)"
}
$allowedRoot = $false
foreach ($root in @($manifest.allowed_verification_roots)) {
    if (Test-PathWithinRoot -Candidate $verificationCanonical -Root ([string]$root)) {
        $allowedRoot = $true
        break
    }
}
if (-not $allowedRoot) {
    throw "VerificationRoot is outside the allowlist: $verificationCanonical"
}

if ($SelfTest) {
    $CommandId = 'self-test'
}
if ([string]::IsNullOrWhiteSpace($CommandId)) {
    throw 'CommandId is required'
}

$command = Get-ManifestCommand -Manifest $manifest -Id $CommandId
Assert-TaskAllowed -Command $command -Id $TaskId
$scratchToolchainOnly = $false
$mutableScratchRoots = @()
$scratchToolchainProperty = $command.PSObject.Properties['scratch_toolchain_only']
if ($null -ne $scratchToolchainProperty) {
    $scratchToolchainOnly = [bool]$scratchToolchainProperty.Value
}
if ($scratchToolchainOnly) {
    $runtimeScratch = Get-CanonicalPath `
        -LiteralPath 'D:\personal\obsidian_workflow-runtime\scratch' `
        -MustExist
    $rootsProperty = $command.PSObject.Properties['mutable_scratch_roots']
    if ($null -eq $rootsProperty -or @($rootsProperty.Value).Count -eq 0) {
        throw "Scratch toolchain command '$CommandId' has no mutable_scratch_roots"
    }
    $mutableScratchRoots = @($rootsProperty.Value | ForEach-Object {
        Assert-NoAlternateDataStreamPath -LiteralPath ([string]$_)
        Assert-NoReparseAncestry -LiteralPath ([string]$_)
        $root = Get-CanonicalPath -LiteralPath ([string]$_) -MustExist
        if (-not (Test-PathAtOrBelowRoot -Candidate $root -Root $runtimeScratch) -or
            $root.Equals($runtimeScratch, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Scratch toolchain root is outside the fixed runtime scratch: $root"
        }
        $root
    })
    $cargoTargetProperty = $command.environment.PSObject.Properties['CARGO_TARGET_DIR']
    if ($null -eq $cargoTargetProperty -or
        -not $mutableScratchRoots.Contains(
            (Get-CanonicalPath -LiteralPath ([string]$cargoTargetProperty.Value) -MustExist)
        )) {
        throw "Scratch toolchain CARGO_TARGET_DIR is not an authorized mutable root"
    }
}
$expectedExitCodes = @(Get-ExpectedExitCodes -Command $command)
$allowedOutputRoots = @()
if (-not $SelfTest) {
    $allowedOutputRoots = @(Get-DeclaredOutputRoots `
        -Command $command `
        -Arguments $ArgumentList `
        -CurrentVerificationRoot $verificationCanonical)
}

$evidencePath = Join-Path $verificationCanonical "evidence-$EvidenceId.json"
$reservationPath = Join-Path $verificationCanonical "reservation-$EvidenceId.json"
if ((Test-Path -LiteralPath $evidencePath) -or (Test-Path -LiteralPath $reservationPath)) {
    throw "Evidence or reservation target already exists for id '$EvidenceId'"
}

if ($SelfTest) {
    # Bootstrap self-test proves the harness mechanics only inside the new
    # GUID root. Repository/private-root coverage is enabled for allowlisted
    # external commands after the bootstrap generation has passed.
    $protectedRoots = @($verificationCanonical)
    $snapshotRoots = @($verificationCanonical)
}
else {
    $protectedRoots = @($manifest.protected_roots | ForEach-Object {
        Assert-NoReparseAncestry -LiteralPath ([string]$_)
        $protectedRoot = Get-CanonicalPath -LiteralPath ([string]$_) -MustExist
        $protectedRoot
    })
    if ($protectedRoots -notcontains $verificationCanonical) {
        $protectedRoots += $verificationCanonical
    }
    $snapshotRootsProperty = $command.PSObject.Properties['snapshot_roots']
    if ($null -eq $snapshotRootsProperty) {
        throw "Command '$CommandId' must declare exact snapshot_roots"
    }
    $snapshotRoots = @($snapshotRootsProperty.Value | ForEach-Object {
        Assert-NoReparseAncestry -LiteralPath ([string]$_)
        $snapshotRoot = Get-CanonicalPath -LiteralPath ([string]$_) -MustExist
        $snapshotRoot
    })
    $snapshotRoots += $verificationCanonical
}

$verificationRootAcl = Add-NoDeleteAcl -LiteralPath $verificationCanonical
$securityBoundaryPaths = New-Object System.Collections.Generic.List[string]
foreach ($protectedRoot in $protectedRoots) {
    $securityBoundaryPaths.Add($protectedRoot)
}
$securityBoundaryPaths.Add($verificationCanonical)
foreach ($outputRoot in $allowedOutputRoots) {
    $currentAncestor = [System.IO.Path]::GetDirectoryName($outputRoot)
    while (-not [string]::IsNullOrWhiteSpace($currentAncestor)) {
        if (Test-Path -LiteralPath $currentAncestor -PathType Container) {
            $insideProtectedRoot = $false
            foreach ($protectedRoot in $protectedRoots) {
                if (Test-PathAtOrBelowRoot -Candidate $currentAncestor -Root $protectedRoot) {
                    $insideProtectedRoot = $true
                    break
                }
            }
            if ($insideProtectedRoot) {
                $securityBoundaryPaths.Add(
                    (Get-CanonicalPath -LiteralPath $currentAncestor -MustExist)
                )
            }
        }
        $reachedProtectedRoot = $false
        foreach ($protectedRoot in $protectedRoots) {
            if ($currentAncestor.Equals(
                $protectedRoot,
                [StringComparison]::OrdinalIgnoreCase
            )) {
                $reachedProtectedRoot = $true
                break
            }
        }
        if ($reachedProtectedRoot) {
            break
        }
        $nextAncestor = [System.IO.Path]::GetDirectoryName($currentAncestor)
        if ([string]::IsNullOrWhiteSpace($nextAncestor) -or
            $nextAncestor.Equals($currentAncestor, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        $currentAncestor = $nextAncestor
    }
}
$securityBoundaryPaths = @($securityBoundaryPaths | Sort-Object -Unique)
$securityBoundaryBefore = @{}
foreach ($securityBoundaryPath in $securityBoundaryPaths) {
    $securityBoundaryBefore[$securityBoundaryPath.ToLowerInvariant()] =
        Get-PathSecurityAttestation -LiteralPath $securityBoundaryPath
}
$before = Get-PathSnapshot -Roots $snapshotRoots
$beforeSnapshotClosureSha256 = Get-SnapshotClosureSha256 -Snapshot $before
$deleteEvents = New-Object System.Collections.Generic.List[string]
$renameEvents = New-Object System.Collections.Generic.List[string]
$createEvents = New-Object System.Collections.Generic.List[string]
$changeEvents = New-Object System.Collections.Generic.List[string]
$allowedAncestorChangeEvents = New-Object System.Collections.Generic.List[string]
$monitorErrors = New-Object System.Collections.Generic.List[string]
$watchers = New-Object System.Collections.Generic.List[System.IO.FileSystemWatcher]
$subscriptions = New-Object System.Collections.Generic.List[object]
$eventSourceIds = New-Object System.Collections.Generic.List[string]

try {
    $watcherIndex = 0
    foreach ($root in $protectedRoots) {
        $watcher = New-Object System.IO.FileSystemWatcher
        $watcher.Path = $root
        $watcher.IncludeSubdirectories = $true
        $watcher.NotifyFilter = [System.IO.NotifyFilters]::FileName -bor
            [System.IO.NotifyFilters]::DirectoryName -bor
            [System.IO.NotifyFilters]::Size -bor
            [System.IO.NotifyFilters]::LastWrite -bor
            [System.IO.NotifyFilters]::Attributes -bor
            [System.IO.NotifyFilters]::Security
        $watcher.InternalBufferSize = 65536
        $deletedSource = "pkm-$EvidenceId-$watcherIndex-deleted"
        $renamedSource = "pkm-$EvidenceId-$watcherIndex-renamed"
        $createdSource = "pkm-$EvidenceId-$watcherIndex-created"
        $changedSource = "pkm-$EvidenceId-$watcherIndex-changed"
        $errorSource = "pkm-$EvidenceId-$watcherIndex-error"
        $subscriptions.Add((Register-ObjectEvent -InputObject $watcher -EventName Deleted -SourceIdentifier $deletedSource))
        $subscriptions.Add((Register-ObjectEvent -InputObject $watcher -EventName Renamed -SourceIdentifier $renamedSource))
        $subscriptions.Add((Register-ObjectEvent -InputObject $watcher -EventName Created -SourceIdentifier $createdSource))
        $subscriptions.Add((Register-ObjectEvent -InputObject $watcher -EventName Changed -SourceIdentifier $changedSource))
        $subscriptions.Add((Register-ObjectEvent -InputObject $watcher -EventName Error -SourceIdentifier $errorSource))
        $eventSourceIds.Add($deletedSource)
        $eventSourceIds.Add($renamedSource)
        $eventSourceIds.Add($createdSource)
        $eventSourceIds.Add($changedSource)
        $eventSourceIds.Add($errorSource)
        $watcher.EnableRaisingEvents = $true
        $watchers.Add($watcher)
        $watcherIndex++
    }

    Write-CreateNewUtf8Json -LiteralPath $reservationPath -Value ([ordered]@{
        schema_version = 1
        task_id = $TaskId
        command_id = $CommandId
        evidence_id = $EvidenceId
        evidence_path = $evidencePath
        mutation_class = 'create-new-with-declared-root-acl-tightening'
        created_at_utc = [DateTime]::UtcNow.ToString('o')
    })
    Assert-NoReparseAncestry -LiteralPath $reservationPath

    if ($SelfTest) {
        $probePath = Join-Path $verificationCanonical "self-test-probe-$EvidenceId.dat"
        Write-CreateNewUtf8Json -LiteralPath $probePath -Value ([ordered]@{
            purpose = 'verification-harness-self-test'
            evidence_id = $EvidenceId
        })
        $processResult = [pscustomobject]@{
            exit_code = 0
            stdout = 'internal self-test passed'
            stderr = ''
            executable_before = $null
            executable_after = $null
            executable_pre_post_match = $null
        }
    }
    else {
        $processResult = Invoke-AllowlistedProcess `
            -Command $command `
            -Executable $FilePath `
            -Arguments $ArgumentList `
            -CurrentVerificationRoot $verificationCanonical `
            -StructuredInput $StructuredInputObject
    }

    Start-Sleep -Milliseconds 150
    $allowedHarnessPaths = @(
        $reservationPath.ToLowerInvariant(),
        $evidencePath.ToLowerInvariant()
    )
    if ($SelfTest) {
        $allowedHarnessPaths += $probePath.ToLowerInvariant()
    }
    foreach ($sourceId in $eventSourceIds) {
        $events = @(Get-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue)
        foreach ($eventRecord in $events) {
            if ($sourceId.EndsWith('-deleted')) {
                [void]$deleteEvents.Add($eventRecord.SourceEventArgs.FullPath)
            }
            elseif ($sourceId.EndsWith('-renamed')) {
                [void]$renameEvents.Add(
                    "$($eventRecord.SourceEventArgs.OldFullPath) -> $($eventRecord.SourceEventArgs.FullPath)"
                )
            }
            elseif ($sourceId.EndsWith('-created')) {
                $eventPath = $eventRecord.SourceEventArgs.FullPath
                if (-not (Test-AllowedMutationPath `
                    -Candidate $eventPath `
                    -ExactPaths $allowedHarnessPaths `
                    -OutputRoots $allowedOutputRoots)) {
                    [void]$createEvents.Add($eventPath)
                }
            }
            elseif ($sourceId.EndsWith('-changed')) {
                $eventPath = $eventRecord.SourceEventArgs.FullPath
                if (Test-AllowedOutputAncestorDirectory `
                    -Candidate $eventPath `
                    -OutputRoots $allowedOutputRoots) {
                    [void]$allowedAncestorChangeEvents.Add($eventPath)
                }
                elseif (-not (Test-AllowedMutationPath `
                    -Candidate $eventPath `
                    -ExactPaths $allowedHarnessPaths `
                    -OutputRoots $allowedOutputRoots)) {
                    [void]$changeEvents.Add($eventPath)
                }
            }
            else {
                [void]$monitorErrors.Add($eventRecord.SourceEventArgs.GetException().Message)
            }
        }
    }
    $manifestAfter = Get-FileAttestation -LiteralPath $manifestCanonical
    $manifestPrePostMatch = Test-AttestationMatch `
        -Before $manifestBefore `
        -After $manifestAfter
    $outputIntegrityErrors = New-Object System.Collections.Generic.List[string]
    foreach ($outputRoot in $allowedOutputRoots) {
        if (-not (Test-Path -LiteralPath $outputRoot)) {
            continue
        }
        try {
            Assert-NoReparseAncestry -LiteralPath $outputRoot
            if (-not (Test-PathWithinRoot `
                -Candidate $outputRoot `
                -Root $verificationCanonical)) {
                [void]$outputIntegrityErrors.Add("OUTPUT_ESCAPED_VERIFICATION_ROOT:$outputRoot")
            }
        }
        catch {
            [void]$outputIntegrityErrors.Add("OUTPUT_REPARSE_OR_IDENTITY_INVALID:$outputRoot")
        }
    }

    try {
        $terminalVerification = Test-ExpectedBlockedTerminal `
            -Command $command `
            -ExitCode ([int]$processResult.exit_code) `
            -Stdout ([string]$processResult.stdout) `
            -Stderr ([string]$processResult.stderr)
    }
    catch {
        $terminalVerification = [pscustomobject]@{
            valid = $false
            reasons = @('EXPECTED_BLOCKED_TERMINAL_VALIDATOR_FAILED')
            terminal = $null
            blockers = @()
        }
    }
    try {
        $successVerification = Test-ExpectedSuccessTerminal `
            -Command $command `
            -Stdout ([string]$processResult.stdout) `
            -Stderr ([string]$processResult.stderr) `
            -BeforeSnapshot $before `
            -CurrentVerificationRoot $verificationCanonical
    }
    catch {
        $successVerification = [pscustomobject]@{
            required = $true
            valid = $false
            reasons = @('EXPECTED_SUCCESS_TERMINAL_VALIDATOR_FAILED')
            terminal = $null
        }
    }

    $snapshotValidationErrors = New-Object System.Collections.Generic.List[string]
    try {
        $after = Get-PathSnapshot -Roots $snapshotRoots
    }
    catch {
        $after = @{}
        [void]$snapshotValidationErrors.Add('POST_PROCESS_SNAPSHOT_FAILED_CLOSED')
    }
    $afterSnapshotClosureSha256 = Get-SnapshotClosureSha256 -Snapshot $after
    $securityBoundaryChanges = New-Object System.Collections.Generic.List[string]
    $securityBoundaryAfter = @{}
    foreach ($securityBoundaryPath in $securityBoundaryPaths) {
        $key = $securityBoundaryPath.ToLowerInvariant()
        try {
            $securityBoundaryAfter[$key] =
                Get-PathSecurityAttestation -LiteralPath $securityBoundaryPath
            $beforeSecurity = $securityBoundaryBefore[$key]
            $afterSecurity = $securityBoundaryAfter[$key]
            if ([string]$beforeSecurity.sddl -cne [string]$afterSecurity.sddl -or
                [string]$beforeSecurity.owner -cne [string]$afterSecurity.owner -or
                [int64]$beforeSecurity.attributes -ne [int64]$afterSecurity.attributes) {
                $securityBoundaryChanges.Add($securityBoundaryPath)
            }
        }
        catch {
            $securityBoundaryChanges.Add($securityBoundaryPath)
        }
    }
    $missing = @($before.Keys | Where-Object { -not $after.ContainsKey($_) })
    $created = @(
        $after.Keys |
            Where-Object {
                -not $before.ContainsKey($_) -and
                -not (Test-AllowedMutationPath `
                    -Candidate $_ `
                    -ExactPaths $allowedHarnessPaths `
                    -OutputRoots $allowedOutputRoots)
            }
    )
    $modified = @(
        $before.Keys |
            Where-Object {
                $after.ContainsKey($_) -and
                -not $before[$_].is_directory -and
                [string]$after[$_].sha256 -ne [string]$before[$_].sha256
            }
    )
    $truncated = @(
        $before.Keys |
            Where-Object {
                $after.ContainsKey($_) -and
                -not $before[$_].is_directory -and
                [long]$after[$_].length -lt [long]$before[$_].length
            }
    )
    $modifiedSecurity = @(
        $before.Keys |
            Where-Object {
                $after.ContainsKey($_) -and (
                    [string]$after[$_].security.sddl -cne [string]$before[$_].security.sddl -or
                    [string]$after[$_].security.owner -cne [string]$before[$_].security.owner -or
                    [int64]$after[$_].security.attributes -ne [int64]$before[$_].security.attributes
                )
            }
    )

    $verificationClean = (
        $expectedExitCodes -contains [int]$processResult.exit_code -and
        [bool]$terminalVerification.valid -and
        [bool]$successVerification.valid -and
        $manifestPrePostMatch -and
        ($null -eq $processResult.executable_pre_post_match -or
            [bool]$processResult.executable_pre_post_match) -and
        $outputIntegrityErrors.Count -eq 0 -and
        $snapshotValidationErrors.Count -eq 0 -and
        $missing.Count -eq 0 -and
        $created.Count -eq 0 -and
        $modified.Count -eq 0 -and
        $deleteEvents.Count -eq 0 -and
        $renameEvents.Count -eq 0 -and
        $createEvents.Count -eq 0 -and
        $changeEvents.Count -eq 0 -and
        $truncated.Count -eq 0 -and
        $modifiedSecurity.Count -eq 0 -and
        $securityBoundaryChanges.Count -eq 0 -and
        $monitorErrors.Count -eq 0
    )

    $observedTerminalStatus = if (-not $verificationClean) {
        'BLOCKED'
    }
    elseif ([int]$processResult.exit_code -ne 0) {
        'VERIFIED_EXPECTED_BLOCKED'
    }
    elseif ($SelfTest) {
        'HARNESS_SELF_TEST_ONLY'
    }
    else {
        'VERIFIED_OBSERVER_ONLY'
    }

    $status = if (-not $verificationClean) {
        'BLOCKED'
    }
    elseif ($TestOnly) {
        'HARNESS_TEST_ONLY'
    }
    else {
        $observedTerminalStatus
    }

    $qualificationBlockers = New-Object System.Collections.Generic.List[string]
    if ($TestOnly) {
        [void]$qualificationBlockers.Add('HARNESS_TEST_ONLY_MANIFEST')
    }
    if (-not $SelfTest) {
        [void]$qualificationBlockers.Add('HARNESS_RESTRICTED_TOKEN_AUTHORITY_ABSENT')
        [void]$qualificationBlockers.Add('HARNESS_JOB_OBJECT_AUTHORITY_ABSENT')
        [void]$qualificationBlockers.Add('HARNESS_DESCENDANT_DRAIN_UNCONFIRMED')
        [void]$qualificationBlockers.Add('HARNESS_EXECUTABLE_SAME_HANDLE_LAUNCH_ABSENT')
        [void]$qualificationBlockers.Add('HARNESS_TIMEOUT_AUTHORITY_ABSENT')
        [void]$qualificationBlockers.Add('HARNESS_BOUNDED_OUTPUT_CAPTURE_ABSENT')
        [void]$qualificationBlockers.Add('HARNESS_TRANSITIVE_TOOLCHAIN_IDENTITY_UNBOUND')
    }
    if ($scratchToolchainOnly) {
        [void]$qualificationBlockers.Add('HARNESS_MUTABLE_SCRATCH_TOOLCHAIN_ONLY')
    }
    foreach ($reason in @($terminalVerification.reasons)) {
        [void]$qualificationBlockers.Add([string]$reason)
    }
    foreach ($reason in @($successVerification.reasons)) {
        [void]$qualificationBlockers.Add([string]$reason)
    }
    if (-not $manifestPrePostMatch) {
        [void]$qualificationBlockers.Add('HARNESS_MANIFEST_PRE_POST_MISMATCH')
    }
    if ($null -ne $processResult.executable_pre_post_match -and
        -not [bool]$processResult.executable_pre_post_match) {
        [void]$qualificationBlockers.Add('HARNESS_EXECUTABLE_PRE_POST_MISMATCH')
    }
    foreach ($outputError in $outputIntegrityErrors) {
        [void]$qualificationBlockers.Add([string]$outputError)
    }
    foreach ($snapshotError in $snapshotValidationErrors) {
        [void]$qualificationBlockers.Add([string]$snapshotError)
    }
    if ($modifiedSecurity.Count -ne 0) {
        [void]$qualificationBlockers.Add('SECURITY_METADATA_CHANGED_DURING_COMMAND')
    }
    if ($securityBoundaryChanges.Count -ne 0) {
        [void]$qualificationBlockers.Add('SECURITY_BOUNDARY_CHANGED_DURING_COMMAND')
    }
    if ($missing.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_SNAPSHOT_PATH_MISSING')
    }
    if ($created.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_UNEXPECTED_PATH_CREATED')
    }
    if ($modified.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_SNAPSHOT_CONTENT_MODIFIED')
    }
    if ($truncated.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_FILE_TRUNCATION_OBSERVED')
    }
    if ($deleteEvents.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_DELETE_EVENT_OBSERVED')
    }
    if ($renameEvents.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_RENAME_EVENT_OBSERVED')
    }
    if ($createEvents.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_UNAUTHORIZED_CREATE_EVENT_OBSERVED')
    }
    if ($changeEvents.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_UNAUTHORIZED_CHANGE_EVENT_OBSERVED')
    }
    if ($monitorErrors.Count -ne 0) {
        [void]$qualificationBlockers.Add('HARNESS_OBSERVER_MONITOR_GAP')
    }
    $stdoutAttestation = Get-Utf8TextAttestation -Text ([string]$processResult.stdout)
    $stderrAttestation = Get-Utf8TextAttestation -Text ([string]$processResult.stderr)

    Write-CreateNewUtf8Json -LiteralPath $evidencePath -Value ([ordered]@{
        schema_version = 1
        task_id = $TaskId
        command_id = $CommandId
        evidence_id = $EvidenceId
        status = $status
        observed_terminal_status = $observedTerminalStatus
        trust_mode = $trustMode
        verification_scope = 'POST_HOC_OBSERVER_ONLY'
        scratch_toolchain_only = $scratchToolchainOnly
        mutable_scratch_roots = $mutableScratchRoots
        qualification_terminal = if ($SelfTest) {
            'HARNESS_SELF_TEST_ONLY'
        }
        else {
            'BLOCKED_TCB_GAP'
        }
        production_qualification_eligible = $false
        process_authority = [ordered]@{
            restricted_token = $false
            job_object = $false
            descendant_drain_confirmed = $false
        }
        qualification_blockers = @($qualificationBlockers | Sort-Object -Unique)
        exit_code = [int]$processResult.exit_code
        expected_exit_codes = $expectedExitCodes
        manifest_generation = [string]$manifest.generation
        release_hash = $manifest.release_hash
        manifest_sha256 = $manifestPreSha256
        manifest_attestation = [ordered]@{
            path = $manifestCanonical
            pre_length = [long]$manifestBefore.length
            post_length = [long]$manifestAfter.length
            pre_creation_time_utc = [string]$manifestBefore.creation_time_utc
            post_creation_time_utc = [string]$manifestAfter.creation_time_utc
            pre_last_write_time_utc = [string]$manifestBefore.last_write_time_utc
            post_last_write_time_utc = [string]$manifestAfter.last_write_time_utc
            pre_sha256 = $manifestPreSha256
            post_sha256 = [string]$manifestAfter.sha256
            pre_post_match = $manifestPrePostMatch
            parsed_from_initial_bytes = $true
        }
        executable_attestation = if ($null -eq $processResult.executable_before) {
            $null
        }
        else {
            [ordered]@{
                path = [string]$processResult.executable_before.path
                pre_length = [long]$processResult.executable_before.length
                post_length = [long]$processResult.executable_after.length
                pre_creation_time_utc = [string]$processResult.executable_before.creation_time_utc
                post_creation_time_utc = [string]$processResult.executable_after.creation_time_utc
                pre_last_write_time_utc = [string]$processResult.executable_before.last_write_time_utc
                post_last_write_time_utc = [string]$processResult.executable_after.last_write_time_utc
                pre_sha256 = [string]$processResult.executable_before.sha256
                post_sha256 = [string]$processResult.executable_after.sha256
                pre_post_match = [bool]$processResult.executable_pre_post_match
                same_handle_launch = $false
                identity_strength = 'PATH_METADATA_HASH_NOT_SAME_HANDLE'
            }
        }
        terminal_verification = [ordered]@{
            valid = [bool]$terminalVerification.valid
            terminal = $terminalVerification.terminal
            blockers = @($terminalVerification.blockers)
            reasons = @($terminalVerification.reasons)
        }
        success_terminal_verification = [ordered]@{
            required = [bool]$successVerification.required
            valid = [bool]$successVerification.valid
            terminal = $successVerification.terminal
            reasons = @($successVerification.reasons)
        }
        spec_sha256 = [string]$manifest.spec_sha256
        started_roots = $protectedRoots
        snapshot_roots = $snapshotRoots
        harness_mutation_policy = @($manifest.harness_mutation_policy)
        verification_root_acl = $verificationRootAcl
        security_boundary_paths = $securityBoundaryPaths
        security_boundary_before = $securityBoundaryBefore
        security_boundary_after = $securityBoundaryAfter
        before_snapshot_closure_sha256 = $beforeSnapshotClosureSha256
        after_snapshot_closure_sha256 = $afterSnapshotClosureSha256
        allowed_output_roots = $allowedOutputRoots
        missing_paths = $missing
        created_paths = $created
        modified_paths = $modified
        delete_events = @($deleteEvents)
        rename_events = @($renameEvents)
        create_events = @($createEvents)
        change_events = @($changeEvents)
        allowed_ancestor_change_events = @($allowedAncestorChangeEvents)
        truncated_paths = $truncated
        modified_security_paths = $modifiedSecurity
        security_boundary_change_paths = @($securityBoundaryChanges)
        monitor_errors = @($monitorErrors)
        output_integrity_errors = @($outputIntegrityErrors)
        snapshot_validation_errors = @($snapshotValidationErrors)
        delete_count = [int]$deleteEvents.Count
        rename_count = [int]$renameEvents.Count
        create_count = [int]$createEvents.Count
        change_count = [int]$changeEvents.Count
        created_path_count = [int]$created.Count
        modified_path_count = [int]$modified.Count
        truncate_count = [int]$truncated.Count
        security_change_count = [int]$modifiedSecurity.Count
        security_boundary_change_count = [int]$securityBoundaryChanges.Count
        monitor_gap_count = [int]$monitorErrors.Count
        stdout_attestation = [ordered]@{
            byte_count = [int64]$stdoutAttestation.byte_count
            sha256 = [string]$stdoutAttestation.sha256
            persisted_raw = $false
        }
        stderr_attestation = [ordered]@{
            byte_count = [int64]$stderrAttestation.byte_count
            sha256 = [string]$stderrAttestation.sha256
            persisted_raw = $false
        }
        completed_at_utc = [DateTime]::UtcNow.ToString('o')
    })
    Assert-NoReparseAncestry -LiteralPath $evidencePath

    if ($status -eq 'BLOCKED') {
        throw "Verification command '$CommandId' was blocked; evidence: $evidencePath"
    }
    Write-Output $evidencePath
}
finally {
    foreach ($watcher in $watchers) {
        $watcher.EnableRaisingEvents = $false
        $watcher.Dispose()
    }
    foreach ($sourceId in $eventSourceIds) {
        Unregister-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
        Remove-Event -SourceIdentifier $sourceId -ErrorAction SilentlyContinue
    }
}
