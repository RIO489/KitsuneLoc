# KitsuneLoc: встановлення / видалення перекладу гри.
# Кладеться в zip програмою (installer.py), запускається з «Встановити переклад.bat»
# (і з -Uninstall — з «Видалити переклад.bat»). Файл має бути UTF-8 з BOM:
# Windows PowerShell 5.1 інакше читає його в ANSI і кирилиця ламається.
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$m = Get-Content -Raw -Encoding UTF8 (Join-Path $here 'patch.json') | ConvertFrom-Json
$host.UI.RawUI.WindowTitle = "Переклад: $($m.title)"

function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Fail($text) { Say ''; Say $text 'Red'; exit 1 }

function Steam-Libraries {
    $roots = @()
    foreach ($k in @(@('HKCU:\Software\Valve\Steam', 'SteamPath'),
                     @('HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
                     @('HKLM:\SOFTWARE\Valve\Steam', 'InstallPath'))) {
        try { $roots += (Get-ItemProperty -Path $k[0] -Name $k[1] -ErrorAction Stop).($k[1]) } catch {}
    }
    $roots += @("${env:ProgramFiles(x86)}\Steam", "$env:ProgramFiles\Steam")
    $libs = @()
    foreach ($r in $roots) {
        if (-not $r -or -not (Test-Path $r)) { continue }
        $libs += $r
        $vdf = Join-Path $r 'steamapps\libraryfolders.vdf'
        if (Test-Path $vdf) {
            foreach ($line in Get-Content -Encoding UTF8 $vdf) {
                if ($line -match '"path"\s+"(.+)"') { $libs += ($Matches[1] -replace '\\\\', '\') }
            }
        }
    }
    return $libs | Select-Object -Unique
}

function Is-Game($dir) { return $dir -and (Test-Path (Join-Path $dir $m.marker)) }

function Find-Game {
    # архів розпакували прямо в теку гри?
    $d = $here
    while ($d) {
        if (Is-Game $d) { return $d }
        $d = Split-Path -Parent $d
    }
    foreach ($lib in Steam-Libraries) {
        $p = Join-Path $lib "steamapps\common\$($m.steam)"
        if (Is-Game $p) { return $p }
    }
    Say "Не знайшов гру «$($m.title)» у Steam. Покажіть її теку у вікні вибору…" 'Yellow'
    Add-Type -AssemblyName System.Windows.Forms
    while ($true) {
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = "Тека гри «$($m.title)» (там, де $($m.exe))"
        if ($dlg.ShowDialog() -ne 'OK') { Fail 'Теку гри не вибрано — нічого не змінено.' }
        if (Is-Game $dlg.SelectedPath) { return $dlg.SelectedPath }
        Say "У цій теці немає гри (не бачу $($m.marker)). Спробуйте ще раз." 'Yellow'
    }
}

function Wait-Closed {
    $name = [IO.Path]::GetFileNameWithoutExtension($m.exe)
    while (Get-Process -Name $name -ErrorAction SilentlyContinue) {
        Say 'Гра зараз запущена. Закрийте її й натисніть Enter.' 'Yellow'
        [void](Read-Host)
    }
}

function Put($src, $dst) {
    # через тимчасовий файл: у теці гри ніколи не лишиться недописаного файлу
    $dir = Split-Path -Parent $dst
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
    Copy-Item -LiteralPath $src -Destination "$dst.new" -Force
    Move-Item -LiteralPath "$dst.new" -Destination $dst -Force
}

Say "Переклад гри «$($m.title)» (KitsuneLoc $($m.kitsuneloc), $($m.made))" 'Cyan'
$root = Find-Game
$data = if ($m.data) { Join-Path $root $m.data } else { $root }
$bk = Join-Path $root 'KitsuneLoc_backup'
$added = Join-Path $bk 'added.txt'
Say "Гра: $root"
Wait-Closed

try {
    if ($Uninstall) {
        if (-not (Test-Path $bk)) { Fail 'Переклад не встановлено (немає теки KitsuneLoc_backup) — нічого робити.' }
        $n = 0
        # усе з KitsuneLoc_backup, а не лише файли цього zip: попередня версія
        # перекладу могла замінити файл, якого в цій уже немає
        $base = (Resolve-Path -LiteralPath $bk).Path.TrimEnd('\') + '\'
        foreach ($o in Get-ChildItem -LiteralPath $bk -Recurse -File) {
            $rel = $o.FullName.Substring($base.Length)
            if ($rel -eq 'added.txt') { continue }
            Put $o.FullName (Join-Path $data $rel); $n++
        }
        if (Test-Path $added) {
            foreach ($rel in Get-Content -Encoding UTF8 $added) {
                $p = Join-Path $data $rel
                if ($rel -and (Test-Path -LiteralPath $p)) { Remove-Item -LiteralPath $p -Force; $n++ }
            }
        }
        Remove-Item -LiteralPath $bk -Recurse -Force
        Say ''
        Say "Готово: повернуто оригінальних файлів — $n. Переклад видалено." 'Green'
        exit 0
    }

    # перевірка: чи файли гри ті самі, для яких зроблено переклад
    $plan = @(); $odd = @()
    $i = 0
    foreach ($f in $m.files) {
        $i++
        Write-Progress -Activity 'Перевіряю файли гри' -Status $f.path -PercentComplete (100 * $i / $m.files.Count)
        $dst = Join-Path $data $f.path
        $orig = Join-Path $bk $f.path
        $have = Test-Path -LiteralPath $dst
        $hash = if ($have) { (Get-FileHash -LiteralPath $dst -Algorithm MD5).Hash } else { '' }
        if ($hash -eq $f.md5) { continue }                      # уже встановлено
        if ($f.orig_md5 -and -not (Test-Path -LiteralPath $orig) -and $hash -ne $f.orig_md5) { $odd += $f.path }
        $plan += [pscustomobject]@{ f = $f; dst = $dst; orig = $orig; have = $have }
    }
    Write-Progress -Activity 'Перевіряю файли гри' -Completed
    if (-not $plan.Count) { Say ''; Say 'Цей переклад уже встановлено — нічого міняти.' 'Green'; exit 0 }
    if ($odd.Count) {
        Say ''
        Say "Увага: $($odd.Count) файл(ів) гри не такі, як ті, для яких зроблено переклад:" 'Yellow'
        $odd | Select-Object -First 10 | ForEach-Object { Say "  $_" 'Yellow' }
        Say 'Можливо, гра іншої версії або її файли вже змінено іншим перекладом/модом.' 'Yellow'
        Say 'Найнадійніше — спершу в Steam «Перевірити цілісність файлів гри».' 'Yellow'
        $a = Read-Host 'Однаково встановити? Введіть 1 і Enter (просто Enter — скасувати)'
        if ($a -ne '1') { Fail 'Скасовано — нічого не змінено.' }
    }

    New-Item -ItemType Directory -Force $bk | Out-Null
    $i = 0
    foreach ($p in $plan) {
        $i++
        Write-Progress -Activity 'Встановлюю переклад' -Status $p.f.path -PercentComplete (100 * $i / $plan.Count)
        if ($p.have -and -not (Test-Path -LiteralPath $p.orig)) {
            $dir = Split-Path -Parent $p.orig
            if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
            Copy-Item -LiteralPath $p.dst -Destination $p.orig -Force
        }
        if (-not $p.have) { Add-Content -Encoding UTF8 -Path $added -Value $p.f.path }
        Put (Join-Path $here ('files\' + $p.f.path)) $p.dst
    }
    Write-Progress -Activity 'Встановлюю переклад' -Completed
    Say ''
    Say "Готово: встановлено файлів — $($plan.Count)." 'Green'
    if ($m.note) { Say $m.note 'Cyan' }
    Say 'Повернути оригінал — «Видалити переклад.bat».'
} catch {
    Fail "Помилка: $($_.Exception.Message)`nМожливо, гра запущена або тека гри лише для читання."
}
