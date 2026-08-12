function Get-SearchTreeHash {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$IncludeFiles
    )

    $ResolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath $ResolvedRoot -PathType Container)) {
        throw "search_tree_hash_root_missing:$ResolvedRoot"
    }

    $Records = [System.Collections.Generic.List[object]]::new()
    foreach ($File in @(Get-ChildItem -LiteralPath $ResolvedRoot -Recurse -File -Force)) {
        if (($File.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "search_tree_hash_reparse_file_not_allowed:$($File.FullName)"
        }
        $FullPath = [System.IO.Path]::GetFullPath($File.FullName)
        $RootPrefix = $ResolvedRoot + [System.IO.Path]::DirectorySeparatorChar
        if (-not $FullPath.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "search_tree_hash_file_outside_root:$FullPath"
        }
        $RelativePath = $FullPath.Substring($RootPrefix.Length).Replace('\', '/')
        [void]$Records.Add([pscustomobject]@{
            relative_path = $RelativePath
            full_path = $File.FullName
            length = [int64]$File.Length
        })
    }

    $Comparison = [System.Comparison[object]]{
        param($Left, $Right)
        $Result = [System.StringComparer]::OrdinalIgnoreCase.Compare(
            [string]$Left.relative_path,
            [string]$Right.relative_path
        )
        if ($Result -eq 0) {
            $Result = [System.StringComparer]::Ordinal.Compare(
                [string]$Left.relative_path,
                [string]$Right.relative_path
            )
        }
        return $Result
    }
    $Records.Sort($Comparison)

    $Encoding = [System.Text.UTF8Encoding]::new($false)
    $Payload = [System.IO.MemoryStream]::new()
    $Writer = [System.IO.BinaryWriter]::new($Payload, $Encoding, $true)
    [int64]$TotalBytes = 0
    $OutputFiles = [System.Collections.Generic.List[object]]::new()
    try {
        $Writer.Write($Encoding.GetBytes("SearchTreeHashV1`0"))
        $Writer.Write([uint64]$Records.Count)
        foreach ($Record in $Records) {
            $PathBytes = $Encoding.GetBytes([string]$Record.relative_path)
            $FileHash = Get-SearchFileSha256 -Path $Record.full_path
            $HashBytes = [byte[]]::new(32)
            for ($Index = 0; $Index -lt 32; $Index += 1) {
                $HashBytes[$Index] = [Convert]::ToByte($FileHash.Substring($Index * 2, 2), 16)
            }
            $Writer.Write([uint64]$PathBytes.Length)
            $Writer.Write($PathBytes)
            $Writer.Write([uint64]$Record.length)
            $Writer.Write($HashBytes)
            $TotalBytes += [int64]$Record.length
            if ($IncludeFiles) {
                [void]$OutputFiles.Add([pscustomobject]@{
                    relative_path = [string]$Record.relative_path
                    length = [int64]$Record.length
                    sha256 = $FileHash
                })
            }
        }
        $Writer.Flush()
        $Payload.Position = 0
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $Digest = $Hasher.ComputeHash($Payload)
        }
        finally {
            $Hasher.Dispose()
        }
    }
    finally {
        $Writer.Dispose()
        $Payload.Dispose()
    }

    $Result = [ordered]@{
        schema_version = "search.tree-hash.v1"
        path_sort = "OrdinalIgnoreCase;OrdinalTieBreak"
        empty_directories = "excluded"
        file_count = $Records.Count
        total_bytes = $TotalBytes
        sha256 = ([System.BitConverter]::ToString($Digest)).Replace("-", "")
    }
    if ($IncludeFiles) { $Result.files = @($OutputFiles) }
    return [pscustomobject]$Result
}

function Get-SearchFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Stream = [System.IO.File]::OpenRead($Path)
    $Hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Digest = $Hasher.ComputeHash($Stream)
        return ([System.BitConverter]::ToString($Digest)).Replace("-", "")
    }
    finally {
        $Hasher.Dispose()
        $Stream.Dispose()
    }
}
