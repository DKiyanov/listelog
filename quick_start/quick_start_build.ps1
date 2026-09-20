# quick_start_build.ps1

# Принудительно загружаем сборку .NET для работы со сжатием
Add-Type -AssemblyName "System.IO.Compression"

$configFile = "quick_start_files.txt"
$zipPath = "../listelog_QuickStart.zip"

# Проверяем наличие конфигурационного файла
if (-not (Test-Path $configFile)) {
    Write-Error "Файл конфигурации $configFile не найден!"
    exit 1
}

# Если архив уже существует, удаляем его перед сборкой
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

# Открываем ZIP-архив для записи с помощью .NET ZipArchive
try {
    # Получаем абсолютный путь к целевой папке для корректной работы .NET файловых потоков
    $absoluteZipPath = New-Object System.IO.FileInfo((Resolve-Path (Split-Path $zipPath)).ProviderPath + "\" + (Split-Path $zipPath -Leaf))
    $zipStream = [System.IO.File]::OpenWrite($absoluteZipPath.FullName)
    $archive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)

    Get-Content $configFile | ForEach-Object {
        $line = $_.Trim()
        
        # Пропускаем пустые строки и комментарии
        if ([string]::IsNullOrEmpty($line) -or $line.StartsWith("#")) {
            return
        }

        # Разделяем строку по символу '>'
        $parts = $line -split '>'
        if ($parts.Count -ne 2) {
            Write-Warning "Пропущена некорректная строка: $line"
            return
        }

        $source = $parts[0].Trim()
        $target = $parts[1].Trim()

        # Нормализуем пути в архиве (заменяем обратные слеши на прямые для ZIP)
        $target = $target.Replace("\", "/")

        if (Test-Path $source) {
            $isDir = (Get-Item $source) -is [System.IO.DirectoryInfo]

            if ($isDir) {
                # Если это каталог, создаем пустую папку в архиве. 
                if (-not $target.EndsWith("/")) { $target += "/" }
                $null = $archive.CreateEntry($target)
                Write-Host "Добавлен каталог: $target"
            } else {
                # Если это файл, добавляем его содержимое
                $entry = $archive.CreateEntry($target)
                $entryStream = $entry.Open()
                $fileStream = [System.IO.File]::OpenRead((Resolve-Path $source).ProviderPath)
                $fileStream.CopyTo($entryStream)
                
                # Закрываем файловые потоки для текущей записи
                $fileStream.Close()
                $entryStream.Close()
                Write-Host "Добавлен файл: $source -> $target"
            }
        } else {
            Write-Warning "Источник не найден и пропущен: $source"
        }
    }
}
finally {
    # Обязательно закрываем архив, чтобы сохранить изменения на диск
    if ($null -ne $archive) { $archive.Dispose() }
    if ($null -ne $zipStream) { $zipStream.Dispose() }
    if (Test-Path $zipPath) {
        Write-Host "Архив успешно создан: $zipPath" -ForegroundColor Green
    }
}
