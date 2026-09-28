# Windows OCR (Windows.Media.Ocr) for KitsuneLoc "Find text on pictures" (textscan.py).
# ASCII only: Windows PowerShell 5.1 reads BOM-less files in the ANSI code page.
#   -check              prints: ok | nolang | noapi
#   -list F -out O      F: text file with PNG paths (UTF-8, one per line)
#                       O: JSON Lines {file, lines:[{text, words:[{text,x,y,w,h}]}]}
#   -serve              (screenwatch.py) read PNG paths from stdin (UTF-8), one per line;
#                       for each write <path>.json {file, lines:[...]} and print 'done'
param([switch]$check, [switch]$serve, [string]$list, [string]$out, [string]$lang = 'en-US')
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    $null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
} catch {
    Write-Output 'noapi'
    exit 0
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $lang))
if ($check) {
    if ($null -eq $engine) { Write-Output 'nolang' } else { Write-Output 'ok' }
    exit 0
}
if ($null -eq $engine) { Write-Output 'nolang'; exit 2 }
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
    $task.Wait() | Out-Null
    $task.Result
}
function Recognize([string]$path) {
    $lines = @()
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        foreach ($l in $res.Lines) {
            $ws = @()
            foreach ($w in $l.Words) {
                $r = $w.BoundingRect
                $ws += @{ text = $w.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
            }
            $lines += @{ text = $l.Text; words = $ws }
        }
    } catch {
        $lines = @()
    } finally {
        # release the file at once: screenwatch writes the next frame right after
        if ($bmp) { $bmp.Dispose() }
        if ($stream) { $stream.Dispose() }
        $bmp = $null; $stream = $null
    }
    return ,$lines
}
if ($serve) {
    [Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    while ($true) {
        $path = [Console]::In.ReadLine()
        if ($null -eq $path -or $path -eq 'quit') { break }
        if (-not $path) { continue }
        $lines = Recognize $path
        $json = @{ file = $path; lines = $lines } | ConvertTo-Json -Depth 6 -Compress
        [System.IO.File]::WriteAllText($path + '.json', $json, $utf8)
        [Console]::Out.WriteLine('done')
        [Console]::Out.Flush()
    }
    exit 0
}
$sw = New-Object System.IO.StreamWriter($out, $false, (New-Object System.Text.UTF8Encoding($false)))
foreach ($path in Get-Content -Encoding UTF8 $list) {
    if (-not $path) { continue }
    $lines = @()
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync([string]$path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        foreach ($l in $res.Lines) {
            $ws = @()
            foreach ($w in $l.Words) {
                $r = $w.BoundingRect
                $ws += @{ text = $w.Text; x = [int]$r.X; y = [int]$r.Y; w = [int]$r.Width; h = [int]$r.Height }
            }
            $lines += @{ text = $l.Text; words = $ws }
        }
        $stream.Dispose()
    } catch {
        $lines = @()
    }
    $sw.WriteLine((@{ file = [string]$path; lines = $lines } | ConvertTo-Json -Depth 6 -Compress))
    $sw.Flush()
    Write-Output ('done ' + [string]$path)
}
$sw.Close()
