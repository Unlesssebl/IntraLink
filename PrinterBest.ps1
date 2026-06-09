Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

#====================================================================================
# НАСТРОЙКИ
#====================================================================================
# Путь к INF файлу драйвера (измените при необходимости)
$global:DriverInfPath = "\\truenas\Drivers\printer\Kyocera\M8124cidn\64bit\OEMSETUP.INF"

# Переменная для хранения всех моделей принтеров
$global:AllPrinterModels = @()

# Переменная для отслеживания успешно включенных WinRM
$global:SuccessfullyEnabledWinRM = @()

# Переменная для отмены операции
$global:CancelOperation = $false

# Переменная для отслеживания текущей операции
$global:CurrentOperation = ""

# Переменная для хранения найденных путей к папке scan
$global:ScanFolderPaths = @{}

# Переменные для асинхронного выполнения
$global:InstallationRunspace = $null
$global:InstallationTimer = $null

# Таймер для валидации IP/имени принтера с задержкой
$global:PrinterValidationTimer = $null

#====================================================================================
# Функция: извлечь модели из INF
#====================================================================================
function Get-PrintersFromInf {
    param([string]$Path)
    if ([string]::IsNullOrEmpty($Path) -or -not (Test-Path $Path)) { return @() }
    try {
        $models = @()
        $content = Get-Content $Path -Raw -ErrorAction Stop
        $pattern = '"(.*?KX)"\s*=\s*\w+,\w*'
        $regex = [regex]::new($pattern, 'IgnoreCase')
        $matchesAll = $regex.Matches($content)
        foreach ($match in $matchesAll) {
            $modelName = $match.Groups[1].Value
            if ($modelName -notlike "*USBPRINT*" -and $modelName -notlike "*WSDPRINT*") {
                $models += $modelName
            }
        }
        return $models | Sort-Object | Get-Unique
    } catch {
        Write-Log "⚠️  Ошибка чтения INF-файла: $($_.Exception.Message)"
        return @()
    }
}

#====================================================================================
# Функция: обновить список моделей
#====================================================================================
function Update-ModelList {
    param([string]$InfPath)
    $comboModel.Items.Clear()
    # Очищаем поле поиска и восстанавливаем placeholder
    $placeholderText = "Введите часть названия модели для поиска..."
    $textSearch.Text = $placeholderText
    $textSearch.ForeColor = [System.Drawing.Color]::Gray
    
    if ([string]::IsNullOrEmpty($InfPath) -or -not (Test-Path $InfPath)) {
        Write-Log "❌ Файл драйвера не найден"
        return
    }
    $PrinterModels = Get-PrintersFromInf -Path $InfPath
    if ($PrinterModels.Count -eq 0) {
        Write-Log "⚠️  Не удалось найти модели в INF-файле"
        return
    }
    
    # Сохраняем все модели в глобальную переменную
    $global:AllPrinterModels = $PrinterModels
    
    # Добавляем все модели в ComboBox
    $comboModel.Items.AddRange($PrinterModels)
    if ($comboModel.Items.Count -gt 0) {
        $comboModel.SelectedIndex = 0
    }
    Write-Log "✅ Модели загружены"
}

#====================================================================================
# Функция: поиск моделей по поисковому запросу
#====================================================================================
function Search-Models {
    param([string]$SearchText)
    
    if ([string]::IsNullOrEmpty($SearchText)) {
        # Если поиск пустой, показываем все модели
        $comboModel.Items.Clear()
        $comboModel.Items.AddRange($global:AllPrinterModels)
    } else {
        # Фильтруем модели по поисковому запросу (регистронезависимый поиск)
        $filteredModels = $global:AllPrinterModels | Where-Object { 
            $_ -like "*$SearchText*" 
        }
        
        $comboModel.Items.Clear()
        $comboModel.Items.AddRange($filteredModels)
    }
    
    # Выбираем первую модель из отфильтрованного списка
    if ($comboModel.Items.Count -gt 0) {
        $comboModel.SelectedIndex = 0
    }
    
    # Обновляем информацию о поиске (без вывода количества)
    if ($global:AllPrinterModels.Count -gt 0) {
        if (-not [string]::IsNullOrEmpty($SearchText)) {
            Write-Log "🔍 Поиск выполнен"
        }
    }
}

#====================================================================================
# Функции валидации
#====================================================================================
function Test-PrinterIP {
    param([string]$IP)
    
    if ([string]::IsNullOrWhiteSpace($IP)) {
        return @{ IsValid = $false; Message = "IP-адрес не может быть пустым" }
    }
    
    # Проверка формата IP
    if (-not ($IP -match '^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$')) {
        return @{ IsValid = $false; Message = "Неверный формат IP-адреса" }
    }
    
    # Проверка доступности (неблокирующая) - оптимизировано с таймаутом
    try {
        $ping = Test-Connection -ComputerName $IP -Count 1 -Quiet -TimeoutSeconds 3 -ErrorAction Stop
        if ($ping) {
            return @{ IsValid = $true; Message = "IP-адрес доступен" }
        } else {
            return @{ IsValid = $true; Message = "IP-адрес недоступен (проверьте подключение)" }
        }
    } catch {
        return @{ IsValid = $true; Message = "Не удалось проверить доступность IP-адреса" }
    }
}

function Test-PrinterAddress {
    param([string]$Address)
    
    if ([string]::IsNullOrWhiteSpace($Address)) {
        return @{ IsValid = $false; Message = "IP-адрес или имя принтера не может быть пустым" }
    }
    
    # Проверка формата IP-адреса
    $isIP = $Address -match '^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    
    if ($isIP) {
        # Это IP-адрес - используем существующую валидацию
        $ipValidation = Test-PrinterIP -IP $Address
        return $ipValidation
    } else {
        # Это имя принтера - проверяем формат имени
        # Имя принтера может содержать буквы, цифры, дефисы, точки и подчеркивания
        # Максимальная длина имени хоста - 255 символов, но для принтера обычно короче
        if ($Address.Length -gt 255) {
            return @{ IsValid = $false; Message = "Имя принтера слишком длинное (максимум 255 символов)" }
        }
        
        # Проверка на допустимые символы в имени хоста
        if ($Address -notmatch '^[a-zA-Z0-9][a-zA-Z0-9\-\._]{0,253}[a-zA-Z0-9]$' -and $Address.Length -gt 1) {
            # Разрешаем имена из одного символа
            if ($Address.Length -eq 1 -and $Address -match '^[a-zA-Z0-9]$') {
                return @{ IsValid = $true; Message = "Имя принтера корректно" }
            }
            return @{ IsValid = $false; Message = "Неверный формат имени принтера (допустимы буквы, цифры, дефисы, точки, подчеркивания)" }
        }
        
        # Проверка доступности по имени (неблокирующая) - оптимизировано с таймаутом
        try {
            $ping = Test-Connection -ComputerName $Address -Count 1 -Quiet -TimeoutSeconds 3 -ErrorAction Stop
            if ($ping) {
                return @{ IsValid = $true; Message = "Имя принтера доступно" }
            } else {
                return @{ IsValid = $true; Message = "Имя принтера недоступно (проверьте подключение)" }
            }
        } catch {
            return @{ IsValid = $true; Message = "Не удалось проверить доступность имени принтера" }
        }
    }
}

function Test-ComputerName {
    param([string]$ComputerName)
    
    if ([string]::IsNullOrWhiteSpace($ComputerName)) {
        return @{ IsValid = $false; Message = "Имя компьютера не может быть пустым" }
    }
    
    # Проверка формата имени компьютера
    if ($ComputerName -notmatch '^[a-zA-Z0-9][a-zA-Z0-9\-\._]{0,14}$') {
        return @{ IsValid = $false; Message = "Неверный формат имени компьютера" }
    }
    
    return @{ IsValid = $true; Message = "Имя компьютера корректно" }
}

function Test-InfFile {
    param([string]$InfPath)
    
    if ([string]::IsNullOrWhiteSpace($InfPath)) {
        return @{ IsValid = $false; Message = "Путь к INF-файлу не может быть пустым" }
    }
    
    if (-not (Test-Path $InfPath)) {
        return @{ IsValid = $false; Message = "INF-файл не найден" }
    }
    
    if ((Get-Item $InfPath).Extension -ne ".inf") {
        return @{ IsValid = $false; Message = "Файл должен иметь расширение .inf" }
    }
    
    # Проверка содержимого INF файла
    try {
        $content = Get-Content $InfPath -Raw -ErrorAction Stop
        if ($content -notmatch '\[Manufacturer\]|\[Models\]') {
            return @{ IsValid = $false; Message = "Файл не является корректным INF-файлом драйвера" }
        }
        return @{ IsValid = $true; Message = "INF-файл корректен" }
    } catch {
        return @{ IsValid = $false; Message = "Ошибка чтения INF-файла: $($_.Exception.Message)" }
    }
}


#====================================================================================
# Создание формы
#====================================================================================
$form = New-Object System.Windows.Forms.Form
$form.Text = "Kyocera Установщик"
$form.Size = New-Object System.Drawing.Size(620, 970)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.MinimizeBox = $true

# Кнопка "Обзор" (в правом верхнем углу)
$buttonBrowse = New-Object System.Windows.Forms.Button
$buttonBrowse.Location = New-Object System.Drawing.Point(520, 10)
$buttonBrowse.Size = New-Object System.Drawing.Size(60, 25)
$buttonBrowse.Text = "Обзор..."
$buttonBrowse.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$buttonBrowse.Add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Filter = "INF-файлы (*.inf)|*.inf|Все файлы (*.*)|*.*"
    $dialog.Title = "Выберите OEMSETUP.INF"

    if (-not [string]::IsNullOrEmpty($global:DriverInfPath)) {
        try {
            $dialog.InitialDirectory = Split-Path $global:DriverInfPath -ErrorAction SilentlyContinue
        } catch { }
    }

    if ($dialog.ShowDialog() -eq "OK") {
        $global:DriverInfPath = $dialog.FileName
        Update-ModelList -InfPath $dialog.FileName
    }
})
$form.Controls.Add($buttonBrowse)


# Модель принтера
$labelModel = New-Object System.Windows.Forms.Label
$labelModel.Location = New-Object System.Drawing.Point(20, 20)
$labelModel.Size = New-Object System.Drawing.Size(150, 20)
$labelModel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelModel)

# Поле поиска модели
$labelSearch = New-Object System.Windows.Forms.Label
$labelSearch.Location = New-Object System.Drawing.Point(20, 45)
$labelSearch.Size = New-Object System.Drawing.Size(150, 20)
$labelSearch.Text = "Поиск модели:"
$labelSearch.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelSearch)

$textSearch = New-Object System.Windows.Forms.TextBox
$textSearch.Location = New-Object System.Drawing.Point(20, 70)
$textSearch.Size = New-Object System.Drawing.Size(560, 25)
$textSearch.Font = New-Object System.Drawing.Font("Segoe UI", 9)

# Placeholder текст (совместимость со старыми .NET версиями)
$placeholderText = "Введите часть названия модели для поиска..."
$textSearch.Text = $placeholderText
$textSearch.ForeColor = [System.Drawing.Color]::Gray

$textSearch.Add_GotFocus({
    if ($textSearch.Text -eq $placeholderText) {
        $textSearch.Text = ""
        $textSearch.ForeColor = [System.Drawing.Color]::Black
    }
})

$textSearch.Add_LostFocus({
    if ([string]::IsNullOrWhiteSpace($textSearch.Text)) {
        $textSearch.Text = $placeholderText
        $textSearch.ForeColor = [System.Drawing.Color]::Gray
    }
})

$textSearch.Add_TextChanged({
    if ($textSearch.Text -ne $placeholderText) {
        Search-Models -SearchText $textSearch.Text
    }
})
$textSearch.Add_KeyDown({
    param($textBox, $e)
    if ($e.KeyCode -eq "Down") {
        $comboModel.Focus()
        $comboModel.DroppedDown = $true
        $e.Handled = $true
    } elseif ($e.KeyCode -eq "Enter") {
        if ($comboModel.Items.Count -gt 0) {
            $comboModel.SelectedIndex = 0
        }
        $e.Handled = $true
    }
})
$form.Controls.Add($textSearch)

$comboModel = New-Object System.Windows.Forms.ComboBox
$comboModel.Location = New-Object System.Drawing.Point(20, 105)
$comboModel.Size = New-Object System.Drawing.Size(560, 25)
$comboModel.DropDownStyle = "DropDownList"
$comboModel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$comboModel.Add_SelectedIndexChanged({
    if ($comboModel.SelectedItem -ne $null) {
        Write-Log "📋 Выбрана модель: $($comboModel.SelectedItem)"
    }
})
$form.Controls.Add($comboModel)

# IP-адрес или имя принтера
$labelIP = New-Object System.Windows.Forms.Label
$labelIP.Location = New-Object System.Drawing.Point(20, 145)
$labelIP.Size = New-Object System.Drawing.Size(200, 20)
$labelIP.Text = "IP-адрес или имя принтера:"
$labelIP.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelIP)

$textIP = New-Object System.Windows.Forms.TextBox
$textIP.Location = New-Object System.Drawing.Point(20, 170)
$textIP.Size = New-Object System.Drawing.Size(560, 25)
$textIP.Text = ""
$textIP.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$textIP.Add_TextChanged({
    # Останавливаем предыдущий таймер, если есть
    if ($global:PrinterValidationTimer) {
        $global:PrinterValidationTimer.Stop()
        $global:PrinterValidationTimer.Dispose()
        $global:PrinterValidationTimer = $null
    }
    
    $address = $textIP.Text.Trim()
    
    # Быстрая проверка формата без задержки
    if ([string]::IsNullOrWhiteSpace($address)) {
        $textIP.BackColor = [System.Drawing.Color]::LightPink
        $textIP.ForeColor = [System.Drawing.Color]::DarkRed
        $labelIPStatus.Text = "IP-адрес или имя принтера не может быть пустым"
        $labelIPStatus.ForeColor = [System.Drawing.Color]::Red
        return
    }
    
    # Проверка формата IP-адреса
    $isIP = $address -match '^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    
    if ($isIP) {
        # Это IP-адрес - проверяем формат
        if (-not ($address -match '^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$')) {
            $textIP.BackColor = [System.Drawing.Color]::LightPink
            $textIP.ForeColor = [System.Drawing.Color]::DarkRed
            $labelIPStatus.Text = "Неверный формат IP-адреса"
            $labelIPStatus.ForeColor = [System.Drawing.Color]::Red
            return
        }
    } else {
        # Это имя принтера - проверяем формат
        if ($address.Length -gt 255) {
            $textIP.BackColor = [System.Drawing.Color]::LightPink
            $textIP.ForeColor = [System.Drawing.Color]::DarkRed
            $labelIPStatus.Text = "Имя принтера слишком длинное (максимум 255 символов)"
            $labelIPStatus.ForeColor = [System.Drawing.Color]::Red
            return
        }
        
        if ($address -notmatch '^[a-zA-Z0-9][a-zA-Z0-9\-\._]{0,253}[a-zA-Z0-9]$' -and $address.Length -gt 1) {
            if ($address.Length -eq 1 -and $address -match '^[a-zA-Z0-9]$') {
                # Один символ - OK, но не проверяем доступность
            } else {
                $textIP.BackColor = [System.Drawing.Color]::LightPink
                $textIP.ForeColor = [System.Drawing.Color]::DarkRed
                $labelIPStatus.Text = "Неверный формат имени принтера"
                $labelIPStatus.ForeColor = [System.Drawing.Color]::Red
                return
            }
        }
    }
    
    # Если формат корректный, показываем "Проверка..." и запускаем таймер
    $textIP.BackColor = [System.Drawing.Color]::White
    $textIP.ForeColor = [System.Drawing.Color]::Black
    $labelIPStatus.Text = "Проверка доступности..."
    $labelIPStatus.ForeColor = [System.Drawing.Color]::Gray
    
    # Создаем таймер с задержкой перед проверкой доступности (оптимизировано)
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 800  # 0.8 секунды (компромисс между скоростью и стабильностью)
    $global:PrinterValidationTimer = $timer
    
    $timer.Add_Tick({
        if ($global:PrinterValidationTimer) {
            $global:PrinterValidationTimer.Stop()
            $global:PrinterValidationTimer.Dispose()
            $global:PrinterValidationTimer = $null
        }
        
        # Выполняем полную проверку доступности
        $printerAddressValidation = Test-PrinterAddress -Address $textIP.Text
        if ($printerAddressValidation.IsValid) {
            $textIP.BackColor = [System.Drawing.Color]::White
            $textIP.ForeColor = [System.Drawing.Color]::Black
            $labelIPStatus.Text = $printerAddressValidation.Message
            $labelIPStatus.ForeColor = [System.Drawing.Color]::Green
        } else {
            $textIP.BackColor = [System.Drawing.Color]::LightPink
            $textIP.ForeColor = [System.Drawing.Color]::DarkRed
            $labelIPStatus.Text = $printerAddressValidation.Message
            $labelIPStatus.ForeColor = [System.Drawing.Color]::Red
        }
    })
    
    $timer.Start()
})
$form.Controls.Add($textIP)

# Метка для отображения статуса валидации IP/имени
$labelIPStatus = New-Object System.Windows.Forms.Label
$labelIPStatus.Location = New-Object System.Drawing.Point(20, 200)
$labelIPStatus.Size = New-Object System.Drawing.Size(560, 20)
$labelIPStatus.Text = ""
$labelIPStatus.Font = New-Object System.Drawing.Font("Segoe UI", 8)
$labelIPStatus.ForeColor = [System.Drawing.Color]::Gray
$form.Controls.Add($labelIPStatus)

# Автоматически отключить WinRM после установки
$checkDisableWinRMAfter = New-Object System.Windows.Forms.CheckBox
$checkDisableWinRMAfter.Location = New-Object System.Drawing.Point(20, 230)
$checkDisableWinRMAfter.Size = New-Object System.Drawing.Size(450, 25)
$checkDisableWinRMAfter.Text = "Автоматически отключить WinRM после установки"
$checkDisableWinRMAfter.Checked = $true
$checkDisableWinRMAfter.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($checkDisableWinRMAfter)

# Компьютеры
$labelPCs = New-Object System.Windows.Forms.Label
$labelPCs.Location = New-Object System.Drawing.Point(20, 270)
$labelPCs.Size = New-Object System.Drawing.Size(300, 20)
$labelPCs.Text = "Имена ПК (по одному на строку):"
$labelPCs.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelPCs)

$textPCs = New-Object System.Windows.Forms.TextBox
$textPCs.Location = New-Object System.Drawing.Point(20, 295)
$textPCs.Size = New-Object System.Drawing.Size(560, 100)
$textPCs.Multiline = $true
$textPCs.ScrollBars = "Vertical"
$textPCs.Font = New-Object System.Drawing.Font("Consolas", 9)
$textPCs.Text = ""
$form.Controls.Add($textPCs)

# Найденные пути к папке scan
$labelScanFolders = New-Object System.Windows.Forms.Label
$labelScanFolders.Location = New-Object System.Drawing.Point(20, 410)
$labelScanFolders.Size = New-Object System.Drawing.Size(300, 20)
$labelScanFolders.Text = "Найденные пути к папке 'scan':"
$labelScanFolders.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelScanFolders)

$listViewScanFolders = New-Object System.Windows.Forms.ListView
$listViewScanFolders.Location = New-Object System.Drawing.Point(20, 435)
$listViewScanFolders.Size = New-Object System.Drawing.Size(560, 150)
$listViewScanFolders.View = [System.Windows.Forms.View]::Details
$listViewScanFolders.FullRowSelect = $true
$listViewScanFolders.GridLines = $true
$listViewScanFolders.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$listViewScanFolders.ShowGroups = $true
$listViewScanFolders.HideSelection = $false

# Колонки
$listViewScanFolders.Columns.Add("Компьютер", 120) | Out-Null
$listViewScanFolders.Columns.Add("Путь", 440) | Out-Null

$form.Controls.Add($listViewScanFolders)

# Обработчик двойного клика для копирования пути
$listViewScanFolders.Add_DoubleClick({
    if ($listViewScanFolders.SelectedItems.Count -gt 0) {
        $selectedItem = $listViewScanFolders.SelectedItems[0]
        $pathToCopy = $selectedItem.Tag
        if ($pathToCopy -and $pathToCopy -ne "") {
            [System.Windows.Forms.Clipboard]::SetText($pathToCopy)
            Write-Log "✅ Путь скопирован в буфер обмена: ${pathToCopy}" -Level "SUCCESS"
        } else {
            Write-Log "⚠️  Нечего копировать для выбранного компьютера" -Level "WARNING"
        }
    }
})

# Лог
$labelLog = New-Object System.Windows.Forms.Label
$labelLog.Location = New-Object System.Drawing.Point(20, 600)
$labelLog.Size = New-Object System.Drawing.Size(150, 20)
$labelLog.Text = "Лог выполнения:"
$labelLog.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelLog)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Location = New-Object System.Drawing.Point(20, 625)
$logBox.Size = New-Object System.Drawing.Size(560, 150)
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = "Vertical"
$logBox.Font = New-Object System.Drawing.Font("Consolas", 8)
$logBox.BackColor = [System.Drawing.Color]::Black
$logBox.ForeColor = [System.Drawing.Color]::White
$form.Controls.Add($logBox)

# Общий прогресс-бар
$labelOverallProgress = New-Object System.Windows.Forms.Label
$labelOverallProgress.Location = New-Object System.Drawing.Point(20, 790)
$labelOverallProgress.Size = New-Object System.Drawing.Size(200, 20)
$labelOverallProgress.Text = "Общий прогресс:"
$labelOverallProgress.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelOverallProgress)

$progressOverall = New-Object System.Windows.Forms.ProgressBar
$progressOverall.Location = New-Object System.Drawing.Point(20, 815)
$progressOverall.Size = New-Object System.Drawing.Size(400, 23)
$progressOverall.Style = "Continuous"
$progressOverall.Minimum = 0
$progressOverall.Maximum = 100
$progressOverall.Value = 0
$form.Controls.Add($progressOverall)

# Прогресс-бар текущей операции
$labelCurrentProgress = New-Object System.Windows.Forms.Label
$labelCurrentProgress.Location = New-Object System.Drawing.Point(20, 845)
$labelCurrentProgress.Size = New-Object System.Drawing.Size(200, 20)
$labelCurrentProgress.Text = "Текущая операция:"
$labelCurrentProgress.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$form.Controls.Add($labelCurrentProgress)

$progressCurrent = New-Object System.Windows.Forms.ProgressBar
$progressCurrent.Location = New-Object System.Drawing.Point(20, 870)
$progressCurrent.Size = New-Object System.Drawing.Size(400, 23)
$progressCurrent.Style = "Continuous"
$progressCurrent.Minimum = 0
$progressCurrent.Maximum = 100
$progressCurrent.Value = 0
$form.Controls.Add($progressCurrent)

# Кнопка отмены
$buttonCancel = New-Object System.Windows.Forms.Button
$buttonCancel.Location = New-Object System.Drawing.Point(480, 805)
$buttonCancel.Size = New-Object System.Drawing.Size(100, 35)
$buttonCancel.Text = "Отмена"
$buttonCancel.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$buttonCancel.BackColor = [System.Drawing.Color]::LightCoral
$buttonCancel.Enabled = $false
$buttonCancel.Add_Click({
    $global:CancelOperation = $true
    Write-Log "⏹️ Операция отменена пользователем"
    $buttonRun.Enabled = $true
    $buttonCancel.Enabled = $false
})
$form.Controls.Add($buttonCancel)

# Кнопка запуска
$buttonRun = New-Object System.Windows.Forms.Button
$buttonRun.Location = New-Object System.Drawing.Point(480, 850)
$buttonRun.Size = New-Object System.Drawing.Size(100, 35)
$buttonRun.Text = "Запустить"
$buttonRun.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$buttonRun.BackColor = [System.Drawing.Color]::LightGreen
$form.Controls.Add($buttonRun)

#====================================================================================
# Функция: запись в лог
#====================================================================================
function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    
    $time = Get-Date -Format "HH:mm:ss"
    $levelPrefix = switch ($Level) {
        "ERROR" { "❌" }
        "WARNING" { "⚠️" }
        "SUCCESS" { "✅" }
        "INFO" { "ℹ️" }
        default { "ℹ️" }
    }
    
    $fullMsg = "[$time] $levelPrefix ${Message}"
    $logBox.AppendText("${fullMsg}`r`n")
    $logBox.SelectionStart = $logBox.Text.Length
    $logBox.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Write-Error-Log {
    param([string]$Message, [object]$Exception = $null)
    
    $time = Get-Date -Format "HH:mm:ss"
    $fullMsg = "[$time] ❌ ERROR: ${Message}"
    
    if ($Exception) {
        # Обрабатываем как ErrorRecord, так и Exception
        if ($Exception -is [System.Management.Automation.ErrorRecord]) {
            $fullMsg += "`n    Детали: $($Exception.Exception.Message)"
            if ($Exception.Exception.InnerException) {
                $fullMsg += "`n    Внутренняя ошибка: $($Exception.Exception.InnerException.Message)"
            }
            $fullMsg += "`n    Стек вызовов: $($Exception.Exception.StackTrace)"
            $fullMsg += "`n    Позиция: $($Exception.InvocationInfo.PositionMessage)"
        } elseif ($Exception -is [System.Exception]) {
            $fullMsg += "`n    Детали: $($Exception.Message)"
            if ($Exception.InnerException) {
                $fullMsg += "`n    Внутренняя ошибка: $($Exception.InnerException.Message)"
            }
            $fullMsg += "`n    Стек вызовов: $($Exception.StackTrace)"
        } else {
            $fullMsg += "`n    Детали: $($Exception.ToString())"
        }
    }
    
    $logBox.AppendText("${fullMsg}`r`n")
    $logBox.SelectionStart = $logBox.Text.Length
    $logBox.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

#====================================================================================
# Функции для обновления прогресс-баров
#====================================================================================
function Update-OverallProgress {
    param([int]$CurrentStep, [int]$TotalSteps, [string]$Operation = "")
    
    if ($TotalSteps -gt 0) {
        $percentage = [math]::Round(($CurrentStep / $TotalSteps) * 100)
        $progressOverall.Value = [math]::Min($percentage, 100)
        
        # Обновляем текст прогресса
        if (-not [string]::IsNullOrEmpty($Operation)) {
            $global:CurrentOperation = $Operation
        }
        
        $labelOverallProgress.Text = "Общий прогресс: $CurrentStep из $TotalSteps ($percentage%)"
        [System.Windows.Forms.Application]::DoEvents()
    }
}

function Update-CurrentProgress {
    param([int]$CurrentStep, [int]$TotalSteps, [string]$Operation = "")
    
    if ($TotalSteps -gt 0) {
        $percentage = [math]::Round(($CurrentStep / $TotalSteps) * 100)
        $progressCurrent.Value = [math]::Min($percentage, 100)
        
        # Обновляем текст текущей операции
        if (-not [string]::IsNullOrEmpty($Operation)) {
            $labelCurrentProgress.Text = "Текущая операция: $Operation ($percentage%)"
        } else {
            $labelCurrentProgress.Text = "Текущая операция: $($global:CurrentOperation) ($percentage%)"
        }
        
        [System.Windows.Forms.Application]::DoEvents()
    }
}

function Reset-ProgressBars {
    $progressOverall.Value = 0
    $progressCurrent.Value = 0
    $labelOverallProgress.Text = "Общий прогресс:"
    $labelCurrentProgress.Text = "Текущая операция:"
    $global:CurrentOperation = ""
    [System.Windows.Forms.Application]::DoEvents()
}

function Set-OperationStatus {
    param([string]$Status)
    $global:CurrentOperation = $Status
    $labelCurrentProgress.Text = "Текущая операция: $Status"
    [System.Windows.Forms.Application]::DoEvents()
}

#====================================================================================
# Функция: включить WinRM на удалённом ПК
#====================================================================================
function Enable-WinRM-Remote {
    param([string]$ComputerName)
    
    Write-Log "➡️  Включение WinRM на ${ComputerName}..."
    [System.Windows.Forms.Application]::DoEvents()
    
    # Метод 1: WMI с улучшенной командой
    try {
        $command = @"
`$ErrorActionPreference='Stop'
try {
    Enable-PSRemoting -Force -SkipNetworkProfileCheck
    Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value `$true -Force
    Set-Item WSMan:\localhost\Client\TrustedHosts -Value '*' -Force
    Set-Service WinRM -StartupType Automatic
    Start-Service WinRM
    Write-Output "SUCCESS"
} catch {
    Write-Output "ERROR: `$(`$_.Exception.Message)"
}
"@
        
        $result = Invoke-WmiMethod -ComputerName ${ComputerName} -Class Win32_Process -Name Create -ArgumentList "powershell.exe -ExecutionPolicy Bypass -Command `"$command`""
        [System.Windows.Forms.Application]::DoEvents()
        
        if ($result.ReturnValue -eq 0) {
            Start-Sleep -Seconds 5
            [System.Windows.Forms.Application]::DoEvents()
            # Проверяем, действительно ли WinRM включился
            if (Test-WSMan -ComputerName ${ComputerName} -ErrorAction SilentlyContinue) {
                Write-Log "✅ WinRM включён на ${ComputerName} (метод 1)"
                return $true
            } else {
                Write-Log "⚠️  Процесс запущен, но WinRM не отвечает на ${ComputerName}"
            }
        } else {
            Write-Log "❌ Метод 1 неуспешен на ${ComputerName} (код: $($result.ReturnValue))"
        }
    } catch {
        Write-Log "❌ Метод 1 неуспешен на ${ComputerName}: $($_.Exception.Message)"
    }
    
    # Метод 2: Альтернативная команда через WMI
    try {
        Write-Log "🔧 Попытка метода 2 на ${ComputerName}..."
        $altCommand = "winrm quickconfig -q & winrm set winrm/config/service @{AllowUnencrypted=`"true`"} & winrm set winrm/config/client @{TrustedHosts=`"*`"}"
        
        $result2 = Invoke-WmiMethod -ComputerName ${ComputerName} -Class Win32_Process -Name Create -ArgumentList "cmd.exe /c `"$altCommand`""
        
        if ($result2.ReturnValue -eq 0) {
            Start-Sleep -Seconds 3
            if (Test-WSMan -ComputerName ${ComputerName} -ErrorAction SilentlyContinue) {
                Write-Log "✅ WinRM включён на ${ComputerName} (метод 2)"
                return $true
            }
        }
    } catch {
        Write-Log "❌ Метод 2 неуспешен на ${ComputerName}: $($_.Exception.Message)"
    }
    
    Write-Log "❌ Не удалось включить WinRM на ${ComputerName} всеми методами"
    return $false
}

#====================================================================================
# Функция: поиск папки "scan" на удалённом ПК
#====================================================================================
function Find-ScanFolder {
    param([string]$ComputerName)
    
    Write-Log "🔍 Поиск папки 'scan' на ${ComputerName}..." -Level "INFO"
    [System.Windows.Forms.Application]::DoEvents()
    
    try {
        $scanPath = $null
        # Проверяем диски C-Z через сетевые административные пути (не требует WinRM)
        for ($driveLetter = [int][char]'C'; $driveLetter -le [int][char]'Z'; $driveLetter++) {
            $drive = [char]$driveLetter
            $networkPath = "\\${ComputerName}\${drive}`$\Scan"
            if (Test-Path -Path $networkPath -PathType Container -ErrorAction SilentlyContinue) {
                $scanPath = "${drive}:\Scan"
                break
            }
        }
        [System.Windows.Forms.Application]::DoEvents()
        
        if ($scanPath) {
            # Преобразуем путь из C:\Scan в формат C$\Scan
            # Буква диска всегда заглавная
            $driveLetter = $scanPath.Substring(0, 1).ToUpper()
            $formattedPath = $driveLetter + '$\Scan'
            $global:ScanFolderPaths[$ComputerName] = $formattedPath
            Write-Log "✅ Найдена папка 'scan' на ${ComputerName}: ${formattedPath}" -Level "SUCCESS"
            
            # Обновляем GUI
            Update-ScanFolderDisplay
            
            return $formattedPath
        } else {
            $global:ScanFolderPaths[$ComputerName] = $null
            Write-Log "⚠️  Папка 'scan' не найдена на ${ComputerName}" -Level "WARNING"
            
            # Обновляем GUI
            Update-ScanFolderDisplay
            
            return $null
        }
    } catch {
        $global:ScanFolderPaths[$ComputerName] = $null
        Write-Log "❌ Ошибка поиска папки 'scan' на ${ComputerName}: $($_.Exception.Message)" -Level "ERROR"
        
        # Обновляем GUI
        Update-ScanFolderDisplay
        
        return $null
    }
}

#====================================================================================
# Функция: обновление отображения найденных путей к папке scan
#====================================================================================
function Update-ScanFolderDisplay {
    # Проверяем, что элемент GUI создан
    if (-not (Get-Variable -Name "listViewScanFolders" -Scope Script -ErrorAction SilentlyContinue)) {
        return
    }
    
    # Очищаем ListView
    $listViewScanFolders.Items.Clear()
    $listViewScanFolders.Groups.Clear()
    
    if ($global:ScanFolderPaths.Count -eq 0) {
        return
    }
    
    # Разделяем компьютеры на найденные и не найденные
    $foundComputers = @()
    $notFoundComputers = @()
    
    foreach ($computer in $global:ScanFolderPaths.Keys | Sort-Object) {
        $path = $global:ScanFolderPaths[$computer]
        if ($path) {
            $foundComputers += @{ Computer = $computer; Path = $path }
        } else {
            $notFoundComputers += @{ Computer = $computer; Path = $null }
        }
    }
    
    # Создаем группы только если есть элементы
    $groupFound = $null
    $groupNotFound = $null
    
    if ($foundComputers.Count -gt 0) {
        $groupFound = New-Object System.Windows.Forms.ListViewGroup("Найдено", "Найдено")
        $listViewScanFolders.Groups.Add($groupFound) | Out-Null
    }
    
    if ($notFoundComputers.Count -gt 0) {
        $groupNotFound = New-Object System.Windows.Forms.ListViewGroup("Не найдено", "Не найдено")
        $listViewScanFolders.Groups.Add($groupNotFound) | Out-Null
    }
    
    # Добавляем найденные
    foreach ($item in $foundComputers) {
        $listItem = New-Object System.Windows.Forms.ListViewItem($item.Computer)
        $listItem.SubItems.Add($item.Path) | Out-Null
        if ($groupFound) {
            $listItem.Group = $groupFound
        }
        $listItem.BackColor = [System.Drawing.Color]::LightGreen
        $listItem.ForeColor = [System.Drawing.Color]::DarkGreen
        $listItem.Tag = $item.Path
        $listViewScanFolders.Items.Add($listItem) | Out-Null
    }
    
    # Добавляем не найденные
    foreach ($item in $notFoundComputers) {
        $listItem = New-Object System.Windows.Forms.ListViewItem($item.Computer)
        $listItem.SubItems.Add("не найдена") | Out-Null
        if ($groupNotFound) {
            $listItem.Group = $groupNotFound
        }
        $listItem.BackColor = [System.Drawing.Color]::LightCoral
        $listItem.ForeColor = [System.Drawing.Color]::DarkRed
        $listItem.Tag = $null
        $listViewScanFolders.Items.Add($listItem) | Out-Null
    }
    
    # Обновляем заголовки групп с количеством элементов
    foreach ($group in $listViewScanFolders.Groups) {
        $group.Header = "$($group.Name) ($($group.Items.Count))"
    }
    
    [System.Windows.Forms.Application]::DoEvents()
}

#====================================================================================
# Функция: отключение службы WinRM на удалённом ПК
#====================================================================================
function Disable-WinRMOnComputer {
    param([string]$ComputerName)

    Write-Log "Обработка: $ComputerName"

    # Проверка доступности компьютера через ping - оптимизировано с таймаутом
    $pingResult = Test-Connection -ComputerName $ComputerName -Count 1 -Quiet -TimeoutSeconds 3

    if (-not $pingResult) {
        Write-Log "❌ Компьютер $ComputerName недоступен (ping неуспешен)"
        return $false
    }

    $operationSuccess = $false
    $errorMessage = ""

    # Метод 1: WMI (исправленный)
    try {
        Write-Log "🔧 Попытка метода 1: WMI..."

        $service = Get-WmiObject -ComputerName $ComputerName -Class Win32_Service -Filter "Name='WinRM'" -ErrorAction Stop

        if ($service) {
            Write-Log "Текущий статус WinRM: $($service.State)"

            if ($service.State -eq "Running") {
                Write-Log "⏹️  Остановка службы WinRM через WMI..."
                $stopResult = $service.StopService()
                if ($stopResult.ReturnValue -eq 0) {
                    Write-Log "Служба остановлена успешно"
                }
                Start-Sleep -Seconds 2
            }

            Write-Log "⚙️  Отключение автозапуска через WMI..."
            $changeResult = $service.ChangeStartMode("Disabled")
            if ($changeResult.ReturnValue -eq 0) {
                Write-Log "Автозапуск отключен успешно"
                $operationSuccess = $true
                Write-Log "✅ Успешно (метод 1): служба WinRM остановлена и отключена"
            }
        } else {
            $errorMessage = "Служба WinRM не найдена через WMI"
        }
    } catch {
        $errorMessage = "Метод 1 неуспешен: $($_.Exception.Message)"
        Write-Log "❌ Метод 1 неуспешен: $($_.Exception.Message)"
    }

    # Метод 2: sc.exe
    if (-not $operationSuccess) {
        try {
            Write-Log "🔧 Попытка метода 2: sc.exe..."

            Write-Log "⏹️  Остановка службы через sc.exe..."
            $stopResult = & sc.exe "\\$ComputerName" stop WinRM 2>$null
            Start-Sleep -Seconds 2

            Write-Log "⚙️  Отключение автозапуска через sc.exe..."
            & sc.exe "\\$ComputerName" config WinRM start= disabled 2>$null

            if ($LASTEXITCODE -eq 0) {
                Write-Log "✅ Успешно (метод 2): служба WinRM остановлена и отключена"
                $operationSuccess = $true
            } else {
                $errorMessage = "Метод 2: sc.exe завершился с кодом $LASTEXITCODE"
            }
        } catch {
            $errorMessage += " | Метод 2 неуспешен: $($_.Exception.Message)"
            Write-Log "❌ Метод 2 неуспешен: $($_.Exception.Message)"
        }
    }

    # Итоговый результат
    if ($operationSuccess) {
        Write-Log "✅ WinRM успешно отключён на $ComputerName"
        return $true
    } else {
        Write-Log "❌ Ошибка отключения WinRM на ${ComputerName}: $errorMessage"
        return $false
    }
}

#====================================================================================
# Функция: получение версии Windows на удалённом ПК
#====================================================================================
function Get-WindowsVersion {
    param([string]$ComputerName)
    
    try {
        # Пытаемся получить версию через WMI
        $os = Get-WmiObject -ComputerName $ComputerName -Class Win32_OperatingSystem -ErrorAction Stop
        
        if ($os) {
            $version = $os.Version
            # Version 6.1 = Windows 7, Version 10.0 = Windows 10
            if ($version -like "6.1*") {
                return "Windows 7"
            } elseif ($version -like "10.0*") {
                return "Windows 10"
            } else {
                return "Windows $version"
            }
        }
    } catch {
        # Если WMI не работает, пробуем через Invoke-Command
        try {
            if (Test-WSMan -ComputerName $ComputerName -ErrorAction SilentlyContinue) {
                $version = Invoke-Command -ComputerName $ComputerName -ScriptBlock {
                    $os = Get-WmiObject -Class Win32_OperatingSystem
                    $os.Version
                } -ErrorAction Stop
                
                if ($version -like "6.1*") {
                    return "Windows 7"
                } elseif ($version -like "10.0*") {
                    return "Windows 10"
                } else {
                    return "Windows $version"
                }
            }
        } catch {
            # Если ничего не получилось, возвращаем "Неизвестно"
            return "Неизвестно"
        }
    }
    
    return "Неизвестно"
}

#====================================================================================
# Функция: выполнение установки в фоновом потоке
#====================================================================================
function Start-InstallationAsync {
    param(
        [string]$SelectedInfPath,
        [string]$SelectedModel,
        [string]$PrinterIP,
        [array]$ValidComputers,
        [bool]$EnableWinRM,
        [bool]$DisableWinRMAfter,
        [string]$PrinterName,
        [string]$DriverName,
        [string]$DriverFolder,
        [string]$RemoteFolderPath,
        [string]$RemoteInfPath,
        [object]$LocalInfHash,
        [bool]$IsIPAddress
    )
    
    # Останавливаем предыдущий таймер, если есть
    if ($global:InstallationTimer) {
        $global:InstallationTimer.Stop()
        $global:InstallationTimer.Dispose()
        $global:InstallationTimer = $null
    }
    
    # Очищаем предыдущий Runspace, если есть
    if ($global:InstallationRunspace) {
        try {
            if ($global:InstallationRunspace.PowerShell) {
                $global:InstallationRunspace.PowerShell.Stop()
                $global:InstallationRunspace.PowerShell.Dispose()
            }
        } catch {}
        $global:InstallationRunspace = $null
    }
    
    # Создаем Runspace для фонового выполнения
    $runspace = [RunspaceFactory]::CreateRunspace()
    $runspace.ApartmentState = "STA"
    $runspace.ThreadOptions = "ReuseThread"
    $runspace.Open()
    
    # Создаем PowerShell instance
    $powershell = [PowerShell]::Create()
    $powershell.Runspace = $runspace
    
    # Создаем скриптблок для выполнения установки
    $scriptBlock = {
        param(
            $SelectedInfPath,
            $SelectedModel,
            $PrinterIP,
            $ValidComputers,
            $EnableWinRM,
            $DisableWinRMAfter,
            $PrinterName,
            $DriverName,
            $DriverFolder,
            $RemoteFolderPath,
            $RemoteInfPath,
            $LocalInfHash,
            $IsIPAddress,
            $CancelOperationRef,
            $SuccessfullyEnabledWinRMRef,
            $ScanFolderPathsRef,
            $FormRef,
            $WriteLogAction,
            $UpdateOverallProgressAction,
            $UpdateCurrentProgressAction,
            $SetOperationStatusAction
        )
        
        $totalComputers = $ValidComputers.Count
        $currentStep = 0
        $successfulInstalls = 0
        $failedInstalls = 0
        $successfulComputers = @()
        $failedComputers = @()
        $alreadyInstalledComputers = @()
        
        $computerIndex = 0
        foreach ($Computer in $ValidComputers) {
            $computerIndex++
            if ($CancelOperationRef.Value) {
                return @{
                    Canceled = $true
                    SuccessfulInstalls = $successfulInstalls
                    FailedInstalls = $failedInstalls
                    SuccessfulComputers = $successfulComputers
                    FailedComputers = $failedComputers
                    AlreadyInstalledComputers = $alreadyInstalledComputers
                    ScanFolderPaths = $ScanFolderPathsRef.Value
                }
            }
            
            # Логируем начало обработки компьютера
            $FormRef.Value.Invoke([System.Action]{
                $WriteLogAction.Invoke("🔄 Начало обработки ПК: $Computer ($computerIndex из $totalComputers)", "INFO")
            })
            
            # Обновляем прогресс
            $FormRef.Value.Invoke([System.Action]{
                $UpdateOverallProgressAction.Invoke($computerIndex, $totalComputers, "Обработка: $Computer")
                $UpdateCurrentProgressAction.Invoke(0, 5, "Проверка доступности...")
                $SetOperationStatusAction.Invoke("Проверка: $Computer")
            })
            
            # Проверка доступности - оптимизировано с таймаутом (Count 2 оставлен для надёжности)
            if (-not (Test-Connection -ComputerName $Computer -Count 2 -Quiet -TimeoutSeconds 3)) {
                $windowsVersion = "Неизвестно"
                try {
                    $os = Get-WmiObject -ComputerName $Computer -Class Win32_OperatingSystem -ErrorAction SilentlyContinue
                    if ($os) {
                        $version = $os.Version
                        if ($version -like "6.1*") { $windowsVersion = "Windows 7" }
                        elseif ($version -like "10.0*") { $windowsVersion = "Windows 10" }
                        else { $windowsVersion = "Windows $version" }
                    }
                } catch {}
                
                $FormRef.Value.Invoke([System.Action]{
                    $WriteLogAction.Invoke("❌ $Computer недоступен ($windowsVersion)", "ERROR")
                })
                
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                $failedInstalls++
                $currentStep += 5
                continue
            }
            
            # Получаем версию Windows
            $windowsVersion = "Неизвестно"
            try {
                $os = Get-WmiObject -ComputerName $Computer -Class Win32_OperatingSystem -ErrorAction SilentlyContinue
                if ($os) {
                    $version = $os.Version
                    if ($version -like "6.1*") { $windowsVersion = "Windows 7" }
                    elseif ($version -like "10.0*") { $windowsVersion = "Windows 10" }
                    else { $windowsVersion = "Windows $version" }
                }
            } catch {}
            
            # Проверка наличия принтера через WMI
            $printerAlreadyInstalled = $false
            try {
                $existingPrinter = Get-WmiObject -ComputerName $Computer -Class Win32_Printer -Filter "Name='$PrinterName'" -ErrorAction SilentlyContinue
                if ($existingPrinter) {
                    $printerAlreadyInstalled = $true
                    $alreadyInstalledComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                    $FormRef.Value.Invoke([System.Action]{
                        $WriteLogAction.Invoke("⚠️ $Computer - установка принтера пропущена, т.к. МФУ уже подключено (принтер '$PrinterName' существует)", "WARNING")
                    })
                }
            } catch {
                # Если WMI для принтеров не работает, продолжаем установку
                $printerAlreadyInstalled = $false
            }
            
            try {
                # Если принтер уже установлен - пропускаем установку, но выполняем поиск scan
                if ($printerAlreadyInstalled) {
                    # Этап 2: Поиск папки scan через сетевые пути (НЕ требует WinRM)
                    $currentStep++
                    $FormRef.Value.Invoke([System.Action]{
                        $UpdateCurrentProgressAction.Invoke(2, 5, "Поиск папки scan...")
                        $SetOperationStatusAction.Invoke("Поиск scan: $Computer")
                    })
                    
                    try {
                        $scanPath = $null
                        # Проверяем диски C-Z через сетевые административные пути
                        for ($driveLetter = [int][char]'C'; $driveLetter -le [int][char]'Z'; $driveLetter++) {
                            $drive = [char]$driveLetter
                            $networkPath = "\\${Computer}\${drive}`$\Scan"
                            if (Test-Path -Path $networkPath -PathType Container -ErrorAction SilentlyContinue) {
                                $scanPath = "${drive}:\Scan"
                                break
                            }
                        }
                        
                        if ($scanPath) {
                            $driveLetter = $scanPath.Substring(0, 1).ToUpper()
                            $formattedPath = $driveLetter + '$\Scan'
                            $ScanFolderPathsRef.Value[$Computer] = $formattedPath
                        } else {
                            $ScanFolderPathsRef.Value[$Computer] = $null
                        }
                    } catch {
                        $ScanFolderPathsRef.Value[$Computer] = $null
                    }
                    
                    # Пропускаем этапы 3, 4, 5 (копирование драйвера, установка принтера, завершение)
                    $currentStep += 3
                    continue
                }
                
                # Этап 1: WinRM (выполняется только если принтер НЕ установлен)
                $currentStep++
                $FormRef.Value.Invoke([System.Action]{
                    $UpdateCurrentProgressAction.Invoke(1, 5, "WinRM...")
                    $SetOperationStatusAction.Invoke("WinRM: $Computer")
                })
                
                if ($EnableWinRM) {
                    if (-not (Test-WSMan -ComputerName $Computer -ErrorAction SilentlyContinue)) {
                        # Упрощенная версия Enable-WinRM-Remote
                        try {
                            $command = @"
`$ErrorActionPreference='Stop'
try {
    Enable-PSRemoting -Force -SkipNetworkProfileCheck
    Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value `$true -Force
    Set-Item WSMan:\localhost\Client\TrustedHosts -Value '*' -Force
    Set-Service WinRM -StartupType Automatic
    Start-Service WinRM
    Write-Output "SUCCESS"
} catch {
    Write-Output "ERROR: `$(`$_.Exception.Message)"
}
"@
                            $result = Invoke-WmiMethod -ComputerName $Computer -Class Win32_Process -Name Create -ArgumentList "powershell.exe -ExecutionPolicy Bypass -Command `"$command`""
                            if ($result.ReturnValue -eq 0) {
                                Start-Sleep -Seconds 5
                                if (Test-WSMan -ComputerName $Computer -ErrorAction SilentlyContinue) {
                                    $SuccessfullyEnabledWinRMRef.Value += $Computer
                                } else {
                                    throw "WinRM не включился"
                                }
                            } else {
                                throw "Не удалось запустить процесс"
                            }
                        } catch {
                            $failedInstalls++
                            $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                            $currentStep += 4
                            continue
                        }
                    } else {
                        $SuccessfullyEnabledWinRMRef.Value += $Computer
                    }
                } else {
                    if (-not (Test-WSMan -ComputerName $Computer -ErrorAction SilentlyContinue)) {
                        $failedInstalls++
                        $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                        $currentStep += 4
                        continue
                    }
                }
                
                # Этап 2: Поиск папки scan через сетевые пути
                $currentStep++
                $FormRef.Value.Invoke([System.Action]{
                    $UpdateCurrentProgressAction.Invoke(2, 5, "Поиск папки scan...")
                    $SetOperationStatusAction.Invoke("Поиск scan: $Computer")
                })
                
                try {
                    $scanPath = $null
                    # Проверяем диски C-Z через сетевые административные пути
                    for ($driveLetter = [int][char]'C'; $driveLetter -le [int][char]'Z'; $driveLetter++) {
                        $drive = [char]$driveLetter
                        $networkPath = "\\${Computer}\${drive}`$\Scan"
                        if (Test-Path -Path $networkPath -PathType Container -ErrorAction SilentlyContinue) {
                            $scanPath = "${drive}:\Scan"
                            break
                        }
                    }
                    
                    if ($scanPath) {
                        $driveLetter = $scanPath.Substring(0, 1).ToUpper()
                        $formattedPath = $driveLetter + '$\Scan'
                        $ScanFolderPathsRef.Value[$Computer] = $formattedPath
                    } else {
                        $ScanFolderPathsRef.Value[$Computer] = $null
                    }
                } catch {
                    $ScanFolderPathsRef.Value[$Computer] = $null
                }
                
                # Этап 3: Копирование драйвера
                $currentStep++
                $FormRef.Value.Invoke([System.Action]{
                    $UpdateCurrentProgressAction.Invoke(3, 5, "Копирование драйвера...")
                    $SetOperationStatusAction.Invoke("Копирование: $Computer")
                })
                
                $DriveLetter = $RemoteFolderPath.Substring(0,1)
                $PathWithoutDrive = $RemoteFolderPath.Substring(2)
                $RemoteAdminPath = "\\${Computer}\${DriveLetter}`$\${PathWithoutDrive}"
                
                try {
                    $remoteFileState = $null
                    $shouldCopy = $true
                    
                    if ($LocalInfHash) {
                        try {
                            $remoteFileState = Invoke-Command -ComputerName $Computer -ScriptBlock {
                                param($path)
                                if (Test-Path $path) {
                                    $hash = Get-FileHash -Path $path -Algorithm 'SHA256'
                                    [PSCustomObject]@{ Exists = $true; Hash = $hash.Hash }
                                } else {
                                    [PSCustomObject]@{ Exists = $false; Hash = $null }
                                }
                            } -ArgumentList $RemoteInfPath -ErrorAction Stop
                        } catch {}
                    }
                    
                    if ($remoteFileState -and $remoteFileState.Exists -and $LocalInfHash -and $remoteFileState.Hash -eq $LocalInfHash.Hash) {
                        $shouldCopy = $false
                    }
                    
                    if ($shouldCopy) {
                        if (-not (Test-Path $RemoteAdminPath)) {
                            New-Item -ItemType Directory -Path $RemoteAdminPath -Force | Out-Null
                        }
                        
                        $robocopyArgs = @(
                            $DriverFolder,
                            $RemoteAdminPath,
                            '/MIR','/FFT','/Z','/NP','/NFL','/NDL','/R:1','/W:1'
                        )
                        
                        & robocopy @robocopyArgs | Out-Null
                        $robocopyExitCode = $LASTEXITCODE
                        
                        if ($robocopyExitCode -gt 7) {
                            throw "robocopy завершился с кодом $robocopyExitCode"
                        }
                        
                        if ($LocalInfHash) {
                            $remoteFileState = Invoke-Command -ComputerName $Computer -ScriptBlock {
                                param($path)
                                if (Test-Path $path) {
                                    $hash = Get-FileHash -Path $path -Algorithm 'SHA256'
                                    [PSCustomObject]@{ Exists = $true; Hash = $hash.Hash }
                                } else {
                                    [PSCustomObject]@{ Exists = $false; Hash = $null }
                                }
                            } -ArgumentList $RemoteInfPath -ErrorAction Stop
                        } else {
                            $remoteFileState = Invoke-Command -ComputerName $Computer -ScriptBlock {
                                param($path)
                                [PSCustomObject]@{ Exists = (Test-Path $path); Hash = $null }
                            } -ArgumentList $RemoteInfPath -ErrorAction Stop
                        }
                    }
                    
                    if (-not $remoteFileState) {
                        $fileExists = Invoke-Command -ComputerName $Computer -ScriptBlock {
                            param($path)
                            Test-Path $path
                        } -ArgumentList $RemoteInfPath -ErrorAction Stop
                    } else {
                        $fileExists = $remoteFileState.Exists
                    }
                    
                    if (-not $fileExists) {
                        throw "Файл драйвера не найден"
                    }
                } catch {
                    $failedInstalls++
                    $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                    $currentStep += 2
                    continue
                }
                
                # Этап 4: Установка принтера
                $currentStep++
                $FormRef.Value.Invoke([System.Action]{
                    $UpdateCurrentProgressAction.Invoke(4, 5, "Установка принтера...")
                    $SetOperationStatusAction.Invoke("Установка: $Computer")
                })
                
                try {
                    $RemoteScript = {
                        param($PrinterAddress, $Name, $Driver, $InfPath)
                        $ErrorActionPreference = 'Stop'
                        $PortName = $PrinterAddress
                        
                        try {
                            if (-not (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue)) {
                                Add-PrinterPort -Name $PortName -PrinterHostAddress $PrinterAddress
                            }
                            
                            if (-not (Get-PrinterDriver -Name $Driver -ErrorAction SilentlyContinue)) {
                                pnputil.exe /add-driver "$InfPath" /install /force 2>&1 | Out-Null
                                Add-PrinterDriver -Name $Driver
                            }
                            
                            if (-not (Get-Printer -Name $Name -ErrorAction SilentlyContinue)) {
                                Add-Printer -Name $Name -PortName $PortName -DriverName $Driver
                            }
                            
                            return $true
                        } catch {
                            return $false
                        }
                    }
                    
                    $result = Invoke-Command -ComputerName $Computer -ScriptBlock $RemoteScript -ArgumentList $PrinterIP, $PrinterName, $DriverName, $RemoteInfPath -ErrorAction Stop
                    
                    $installSuccess = $result -eq $true
                    if ($installSuccess) {
                        $successfulInstalls++
                        $successfulComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                        $FormRef.Value.Invoke([System.Action]{
                            $WriteLogAction.Invoke("✅ $Computer - принтер установлен", "SUCCESS")
                        })
                    } else {
                        $failedInstalls++
                        $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                        $FormRef.Value.Invoke([System.Action]{
                            $WriteLogAction.Invoke("❌ $Computer - ошибка установки", "ERROR")
                        })
                    }
                } catch {
                    $failedInstalls++
                    $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                    $FormRef.Value.Invoke([System.Action]{
                        $WriteLogAction.Invoke("❌ $Computer - ошибка: $($_.Exception.Message)", "ERROR")
                    })
                }
                
                # Этап 5: Завершение
                $currentStep++
                $FormRef.Value.Invoke([System.Action]{
                    $UpdateCurrentProgressAction.Invoke(5, 5, "Завершено")
                })
                
            } catch {
                $failedInstalls++
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                $completedStepsForThisComputer = ($currentStep - 1) % 5
                $remainingStepsForThisComputer = 5 - $completedStepsForThisComputer
                $currentStep += $remainingStepsForThisComputer
            }
        }
        
        # Отключение WinRM
        if ($DisableWinRMAfter -and $SuccessfullyEnabledWinRMRef.Value.Count -gt 0) {
            foreach ($Computer in $SuccessfullyEnabledWinRMRef.Value) {
                try {
                    $service = Get-WmiObject -ComputerName $Computer -Class Win32_Service -Filter "Name='WinRM'" -ErrorAction SilentlyContinue
                    if ($service -and $service.State -eq "Running") {
                        $service.StopService() | Out-Null
                        Start-Sleep -Seconds 2
                    }
                    if ($service) {
                        $service.ChangeStartMode("Disabled") | Out-Null
                    }
                } catch {}
            }
        }
        
        return @{
            Canceled = $false
            SuccessfulInstalls = $successfulInstalls
            FailedInstalls = $failedInstalls
            SuccessfulComputers = $successfulComputers
            FailedComputers = $failedComputers
            AlreadyInstalledComputers = $alreadyInstalledComputers
            ScanFolderPaths = $ScanFolderPathsRef.Value
            SuccessfullyEnabledWinRM = $SuccessfullyEnabledWinRMRef.Value
        }
    }
    
    # Создаем ссылки для передачи в скриптблок
    $cancelRef = [ref]$global:CancelOperation
    $winrmRef = [ref]$global:SuccessfullyEnabledWinRM
    $scanRef = [ref]$global:ScanFolderPaths
    $formRef = [ref]$form
    
    # Создаем действия для обновления UI
    $writeLogAction = {
        param([string]$Message, [string]$Level = "INFO")
        Write-Log -Message $Message -Level $Level
    }
    
    $updateOverallProgressAction = {
        param([int]$CurrentStep, [int]$TotalSteps, [string]$Operation = "")
        Update-OverallProgress -CurrentStep $CurrentStep -TotalSteps $TotalSteps -Operation $Operation
    }
    
    $updateCurrentProgressAction = {
        param([int]$CurrentStep, [int]$TotalSteps, [string]$Operation = "")
        Update-CurrentProgress -CurrentStep $CurrentStep -TotalSteps $TotalSteps -Operation $Operation
    }
    
    $setOperationStatusAction = {
        param([string]$Status)
        Set-OperationStatus -Status $Status
    }
    
    # Добавляем параметры
    $null = $powershell.AddScript($scriptBlock)
    $null = $powershell.AddArgument($SelectedInfPath)
    $null = $powershell.AddArgument($SelectedModel)
    $null = $powershell.AddArgument($PrinterIP)
    $null = $powershell.AddArgument($ValidComputers)
    $null = $powershell.AddArgument($EnableWinRM)
    $null = $powershell.AddArgument($DisableWinRMAfter)
    $null = $powershell.AddArgument($PrinterName)
    $null = $powershell.AddArgument($DriverName)
    $null = $powershell.AddArgument($DriverFolder)
    $null = $powershell.AddArgument($RemoteFolderPath)
    $null = $powershell.AddArgument($RemoteInfPath)
    $null = $powershell.AddArgument($LocalInfHash)
    $null = $powershell.AddArgument($IsIPAddress)
    $null = $powershell.AddArgument($cancelRef)
    $null = $powershell.AddArgument($winrmRef)
    $null = $powershell.AddArgument($scanRef)
    $null = $powershell.AddArgument($formRef)
    $null = $powershell.AddArgument($writeLogAction)
    $null = $powershell.AddArgument($updateOverallProgressAction)
    $null = $powershell.AddArgument($updateCurrentProgressAction)
    $null = $powershell.AddArgument($setOperationStatusAction)
    
    # Запускаем асинхронно
    $handle = $powershell.BeginInvoke()
    
    $global:InstallationRunspace = @{
        PowerShell = $powershell
        Handle = $handle
        Runspace = $runspace
    }
    
    # Создаем таймер для проверки завершения и обновления UI
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 100  # Проверяем каждые 100мс
    $global:InstallationTimer = $timer
    
    $timer.Add_Tick({
        if ($global:InstallationRunspace -and $global:InstallationRunspace.Handle.IsCompleted) {
            if ($global:InstallationTimer) {
                $global:InstallationTimer.Stop()
            }
            
            try {
                $result = $global:InstallationRunspace.PowerShell.EndInvoke($global:InstallationRunspace.Handle)
                
                # Обновляем UI безопасно
                $form.Invoke([System.Action]{
                    # Обновляем отображение папок scan
                    Update-ScanFolderDisplay
                    
                    # Вычисляем общее количество компьютеров из результата
                    $totalComputers = $result.SuccessfulComputers.Count + $result.FailedComputers.Count
                    $totalSteps = $totalComputers * 5
                    
                    # Завершаем прогресс-бары
                    Update-OverallProgress -CurrentStep $totalComputers -TotalSteps $totalComputers -Operation "Завершено"
                    Update-CurrentProgress -CurrentStep $totalSteps -TotalSteps $totalSteps -Operation "Готово"
                    
                    # Восстанавливаем заголовок
                    $form.Text = "Kyocera Установщик"
                    
                    # Логируем результаты
                    Write-Log "=== УСТАНОВКА ЗАВЕРШЕНА ===" -Level "INFO"
                    Write-Log "Успешно установлено: $($result.SuccessfulInstalls)" -Level "SUCCESS"
                    Write-Log "Ошибок установки: $($result.FailedInstalls)" -Level "ERROR"
                    if ($result.AlreadyInstalledComputers -and $result.AlreadyInstalledComputers.Count -gt 0) {
                        Write-Log "Пропущено (МФУ уже подключено): $($result.AlreadyInstalledComputers.Count)" -Level "WARNING"
                    }
                    
                    Write-Log "=== ИТОГОВЫЙ ОТЧЕТ ===" -Level "INFO"
                    
                    if ($result.SuccessfulComputers.Count -gt 0) {
                        Write-Log "Успешно установлено ($($result.SuccessfulComputers.Count)):" -Level "SUCCESS"
                        foreach ($pc in $result.SuccessfulComputers) {
                            Write-Log "  ✅ $($pc.Computer) ($($pc.WindowsVersion))" -Level "SUCCESS"
                        }
                    }
                    
                    if ($result.AlreadyInstalledComputers -and $result.AlreadyInstalledComputers.Count -gt 0) {
                        Write-Log "МФУ уже подключено ($($result.AlreadyInstalledComputers.Count)):" -Level "WARNING"
                        foreach ($pc in $result.AlreadyInstalledComputers) {
                            Write-Log "  ⚠️ $($pc.Computer) ($($pc.WindowsVersion))" -Level "WARNING"
                        }
                    }
                    
                    if ($result.FailedComputers.Count -gt 0) {
                        Write-Log "Ошибки установки ($($result.FailedComputers.Count)):" -Level "ERROR"
                        foreach ($pc in $result.FailedComputers) {
                            Write-Log "  ❌ $($pc.Computer) ($($pc.WindowsVersion))" -Level "ERROR"
                        }
                    }
                    
                    if ($result.Canceled) {
                        Write-Log "Операция была отменена пользователем" -Level "WARNING"
                    }
                    
                    # Показываем итоговое сообщение
                    $message = "Установка завершена!`n`nУспешно: $($result.SuccessfulInstalls)`nОшибок: $($result.FailedInstalls)"
                    if ($result.AlreadyInstalledComputers -and $result.AlreadyInstalledComputers.Count -gt 0) {
                        $message += "`nПропущено (МФУ уже подключено): $($result.AlreadyInstalledComputers.Count)"
                    }
                    if ($result.Canceled) {
                        $message += "`n`nОперация была отменена пользователем."
                    }
                    [System.Windows.Forms.MessageBox]::Show($message, "Результат установки", "OK", "Information")
                })
                
            } catch {
                $form.Invoke([System.Action]{
                    Write-Error-Log "Ошибка при завершении установки" -Exception $_
                })
            } finally {
                # Очищаем ресурсы
                if ($global:InstallationRunspace) {
                    try {
                        if ($global:InstallationRunspace.PowerShell) {
                            $global:InstallationRunspace.PowerShell.Dispose()
                        }
                        if ($global:InstallationRunspace.Runspace) {
                            $global:InstallationRunspace.Runspace.Close()
                            $global:InstallationRunspace.Runspace.Dispose()
                        }
                    } catch {}
                    $global:InstallationRunspace = $null
                }
                
                # Восстанавливаем UI
                $form.Invoke([System.Action]{
                    $form.Cursor = [System.Windows.Forms.Cursors]::Default
                    $form.UseWaitCursor = $false
                    $buttonRun.Enabled = $true
                    $buttonCancel.Enabled = $false
                    $global:CancelOperation = $false
                })
            }
            
            if ($global:InstallationTimer) {
                $global:InstallationTimer.Dispose()
                $global:InstallationTimer = $null
            }
        } else {
            # Обновляем прогресс во время выполнения
            # (можно добавить более детальное отслеживание прогресса)
            [System.Windows.Forms.Application]::DoEvents()
        }
    })
    
    $timer.Start()
}

#====================================================================================
# Обработчик кнопки "Запустить"
#====================================================================================
$buttonRun.Add_Click({
    try {
        # Сброс состояния
        $global:CancelOperation = $false
        $buttonRun.Enabled = $false
        $buttonCancel.Enabled = $true
        
        # Устанавливаем курсор ожидания для всей формы
        $form.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
        $form.UseWaitCursor = $true
        [System.Windows.Forms.Application]::DoEvents()
        
        # Получение данных из формы
        $selectedInfPath = $global:DriverInfPath.Trim()
        $selectedModel = $comboModel.SelectedItem
        $printerIP = $textIP.Text.Trim()
        $computers = $textPCs.Lines | Where-Object { $_.Trim() -ne "" } | ForEach-Object { $_.Trim() }
        $enableWinRM = $true  # WinRM всегда включается по умолчанию
        $disableWinRMAfter = $checkDisableWinRMAfter.Checked

        Write-Log "=== НАЧАЛО ВАЛИДАЦИИ ===" -Level "INFO"

        # Валидация INF файла
        $infValidation = Test-InfFile -InfPath $selectedInfPath
        if (-not $infValidation.IsValid) {
            Write-Log "Ошибка валидации INF: $($infValidation.Message)" -Level "ERROR"
            [System.Windows.Forms.MessageBox]::Show("Ошибка INF-файла: $($infValidation.Message)", "Ошибка валидации", "OK", "Error")
            $buttonBrowse.Focus()
            $buttonRun.Enabled = $true
            $buttonCancel.Enabled = $false
            return
        }
        Write-Log "INF-файл валиден: $($infValidation.Message)" -Level "SUCCESS"

        # Валидация модели принтера
        if (-not $selectedModel) {
            Write-Log "Модель принтера не выбрана" -Level "ERROR"
            [System.Windows.Forms.MessageBox]::Show("Выберите модель принтера.", "Ошибка валидации", "OK", "Error")
            $comboModel.Focus()
            $buttonRun.Enabled = $true
            $buttonCancel.Enabled = $false
            return
        }
        Write-Log "Модель принтера выбрана: $selectedModel" -Level "SUCCESS"

        # Валидация IP-адреса или имени принтера
        $printerAddressValidation = Test-PrinterAddress -Address $printerIP
        if (-not $printerAddressValidation.IsValid) {
            Write-Log "Ошибка валидации IP/имени: $($printerAddressValidation.Message)" -Level "ERROR"
            [System.Windows.Forms.MessageBox]::Show("Ошибка IP-адреса/имени принтера: $($printerAddressValidation.Message)", "Ошибка валидации", "OK", "Error")
            $textIP.Focus()
            $buttonRun.Enabled = $true
            $buttonCancel.Enabled = $false
            return
        }
        Write-Log "IP-адрес/имя принтера валидно: $($printerAddressValidation.Message)" -Level "SUCCESS"

        # Валидация списка компьютеров
        if ($computers.Count -eq 0) {
            Write-Log "Список компьютеров пуст" -Level "ERROR"
            [System.Windows.Forms.MessageBox]::Show("Введите хотя бы один компьютер.", "Ошибка валидации", "OK", "Error")
            $textPCs.Focus()
            $buttonRun.Enabled = $true
            $buttonCancel.Enabled = $false
            return
        }

        # Валидация каждого компьютера
        $validComputers = @()
        foreach ($computer in $computers) {
            $computerValidation = Test-ComputerName -ComputerName $computer
            if ($computerValidation.IsValid) {
                $validComputers += $computer
                Write-Log "Компьютер '$computer' валиден" -Level "SUCCESS"
            } else {
                Write-Log "Компьютер '$computer' невалиден: $($computerValidation.Message)" -Level "WARNING"
            }
        }

        if ($validComputers.Count -eq 0) {
            Write-Log "Нет валидных компьютеров для обработки" -Level "ERROR"
            [System.Windows.Forms.MessageBox]::Show("Нет валидных компьютеров для обработки.", "Ошибка валидации", "OK", "Error")
            $textPCs.Focus()
            $buttonRun.Enabled = $true
            $buttonCancel.Enabled = $false
            return
        }

        Write-Log "Валидация завершена успешно. Валидных компьютеров: $($validComputers.Count)" -Level "SUCCESS"

        # Подготовка данных для установки
        # Определяем, является ли введенное значение IP-адресом или именем
        $isIPAddress = $printerIP -match '^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
        
        if ($isIPAddress) {
            # Если это IP-адрес, формируем короткое имя как раньше
            $ipParts = $printerIP -split '\.'
            $shortIP = "$($ipParts[2]).$($ipParts[3])"
            
            # Формируем имя принтера на основе выбранной модели
            $modelDisplayName = $selectedModel
            if ($modelDisplayName -like "Kyocera *") {
                $modelDisplayName = $modelDisplayName -replace "^Kyocera\s*", ""
            }
            
            $PrinterName = "${modelDisplayName} (${shortIP})"
        } else {
            # Если это имя принтера, используем его напрямую
            $modelDisplayName = $selectedModel
            if ($modelDisplayName -like "Kyocera *") {
                $modelDisplayName = $modelDisplayName -replace "^Kyocera\s*", ""
            }
            
            $PrinterName = "${modelDisplayName} (${printerIP})"
        }

        $global:DriverInfPath = $selectedInfPath
        $global:SuccessfullyEnabledWinRM = @()
        $global:ScanFolderPaths = @{}  # Сброс результатов поиска папки scan
        
        # Очищаем поле отображения найденных путей
        Update-ScanFolderDisplay
        
        # Инициализация прогресс-баров
        Reset-ProgressBars
        $totalComputers = $validComputers.Count
        $totalSteps = $totalComputers * 5  # 5 этапов на компьютер (добавлен поиск папки scan)
        
        Write-Log "=== НАЧАЛО УСТАНОВКИ ===" -Level "INFO"
        Write-Log "Модель: ${selectedModel}" -Level "INFO"
        if ($isIPAddress) {
            Write-Log "IP-адрес: ${printerIP}" -Level "INFO"
        } else {
            Write-Log "Имя принтера: ${printerIP}" -Level "INFO"
        }
        Write-Log "Имя принтера в системе: ${PrinterName}" -Level "INFO"
        Write-Log "ПК: $($validComputers -join ', ')" -Level "INFO"
        Write-Log "Папка драйвера: $(Split-Path $selectedInfPath -Parent)" -Level "INFO"

        $DriverName = $selectedModel
        $DriverFolder = Split-Path $selectedInfPath -Parent
        $RemoteFolderPath = "C:\Windows\Temp\Kyocera_M2040dn"
        $RemoteInfPath = "${RemoteFolderPath}\$(Split-Path $selectedInfPath -Leaf)"

        $localInfHash = $null
        try {
            $localInfHash = Get-FileHash -Path $selectedInfPath -Algorithm 'SHA256'
            Write-Log "Хэш INF-файла: $($localInfHash.Hash)" -Level "INFO"
        } catch {
            Write-Log "Не удалось вычислить хэш INF-файла: $($_.Exception.Message). Копирование будет без проверки версии." -Level "WARNING"
        }

        # Запускаем установку в асинхронном режиме
        Start-InstallationAsync `
            -SelectedInfPath $selectedInfPath `
            -SelectedModel $selectedModel `
            -PrinterIP $printerIP `
            -ValidComputers $validComputers `
            -EnableWinRM $enableWinRM `
            -DisableWinRMAfter $disableWinRMAfter `
            -PrinterName $PrinterName `
            -DriverName $DriverName `
            -DriverFolder $DriverFolder `
            -RemoteFolderPath $RemoteFolderPath `
            -RemoteInfPath $RemoteInfPath `
            -LocalInfHash $localInfHash `
            -IsIPAddress $isIPAddress
        
        # Обновляем прогресс-бары для отображения начала работы
        $totalComputers = $validComputers.Count
        $totalSteps = $totalComputers * 5
        Reset-ProgressBars
        
        # Основной цикл установки теперь выполняется в фоновом потоке
        return
        
    } catch {
        Write-Error-Log "Критическая ошибка в основном обработчике" -Exception $_
        [System.Windows.Forms.MessageBox]::Show("Произошла критическая ошибка: $($_.Exception.Message)", "Ошибка", "OK", "Error")

            # Проверка доступности - оптимизировано с таймаутом (Count 2 оставлен для надёжности)
            Set-OperationStatus "Проверка доступности ${Computer}"
            [System.Windows.Forms.Application]::DoEvents()
            if (-not (Test-Connection -ComputerName ${Computer} -Count 2 -Quiet -TimeoutSeconds 3)) {
                Write-Log "❌ ${Computer}: недоступен" -Level "ERROR"
                # Получаем версию Windows для недоступного ПК (может не сработать, но попробуем)
                $windowsVersion = Get-WindowsVersion -ComputerName ${Computer}
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                $failedInstalls++
                $currentStep += 5
                Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
                continue
            }
            Write-Log "✅ ${Computer}: доступен" -Level "SUCCESS"
            
            # Получаем версию Windows для доступного ПК
            $windowsVersion = Get-WindowsVersion -ComputerName ${Computer}
            Write-Log "📋 ${Computer}: версия ОС - ${windowsVersion}" -Level "INFO"

            try {

            # Этап 1: Включить WinRM при необходимости
            $currentStep++
            Set-OperationStatus "Настройка WinRM на ${Computer}"
            Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
            
            try {
                if ($enableWinRM) {
                    if (-not (Test-WSMan -ComputerName ${Computer} -ErrorAction SilentlyContinue)) {
                        if (Enable-WinRM-Remote -ComputerName ${Computer}) {
                            $global:SuccessfullyEnabledWinRM += $Computer
                            Write-Log "WinRM успешно включён на ${Computer}" -Level "SUCCESS"
                        } else {
                            Write-Log "Не удалось включить WinRM на ${Computer}" -Level "ERROR"
                            $failedInstalls++
                            $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                            $currentStep += 4
                            Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
                            continue
                        }
                    } else {
                        Write-Log "WinRM уже включён на ${Computer}" -Level "SUCCESS"
                        $global:SuccessfullyEnabledWinRM += $Computer
                    }
                } else {
                    if (-not (Test-WSMan -ComputerName ${Computer} -ErrorAction SilentlyContinue)) {
                        Write-Log "WinRM не включён на ${Computer}" -Level "ERROR"
                        $failedInstalls++
                        $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                        $currentStep += 4
                        Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
                        continue
                    }
                }
            } catch {
                Write-Error-Log "Ошибка настройки WinRM на ${Computer}" -Exception $_
                $failedInstalls++
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                $currentStep += 4
                Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
                continue
            }

            # Этап 2: Поиск папки "scan"
            $currentStep++
            Set-OperationStatus "Поиск папки 'scan' на ${Computer}"
            Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
            
            try {
                Find-ScanFolder -ComputerName ${Computer}
            } catch {
                Write-Error-Log "Ошибка поиска папки 'scan' на ${Computer}" -Exception $_
                # Не прерываем выполнение, продолжаем установку
            }

            # Подготовка пути на удалённой машине
            $DriveLetter = $RemoteFolderPath.Substring(0,1)
            $PathWithoutDrive = $RemoteFolderPath.Substring(2)
            $RemoteAdminPath = "\\${Computer}\${DriveLetter}`$\${PathWithoutDrive}"

            # Этап 3: Копирование файлов драйвера
            $currentStep++
            Set-OperationStatus "Копирование драйвера на ${Computer}"
            Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
            
            try {
                $remoteFileState = $null
                $shouldCopy = $true

                if ($localInfHash) {
                    try {
                        $remoteFileState = Invoke-Command -ComputerName $Computer -ScriptBlock {
                            param($path)
                            if (Test-Path $path) {
                                $hash = Get-FileHash -Path $path -Algorithm 'SHA256'
                                [PSCustomObject]@{ Exists = $true; Hash = $hash.Hash }
                            } else {
                                [PSCustomObject]@{ Exists = $false; Hash = $null }
                            }
                        } -ArgumentList $RemoteInfPath -ErrorAction Stop
                    } catch {
                        Write-Log "Не удалось проверить версию драйвера на ${Computer}: $($_.Exception.Message)" -Level "WARNING"
                    }
                }

                if ($remoteFileState -and $remoteFileState.Exists -and $localInfHash -and $remoteFileState.Hash -eq $localInfHash.Hash) {
                    Write-Log "Драйвер на ${Computer} уже актуален — копирование пропущено" -Level "SUCCESS"
                    $shouldCopy = $false
                } elseif ($remoteFileState -and $remoteFileState.Exists) {
                    Write-Log "На ${Computer} обнаружена другая версия драйвера — выполняем синхронизацию" -Level "INFO"
                } else {
                    Write-Log "Драйвер на ${Computer} отсутствует — выполняем копирование" -Level "INFO"
                }

                if ($shouldCopy) {
                    if (-not (Test-Path $RemoteAdminPath)) {
                        New-Item -ItemType Directory -Path $RemoteAdminPath -Force | Out-Null
                    }

                    $robocopyArgs = @(
                        $DriverFolder,
                        $RemoteAdminPath,
                        '/MIR','/FFT','/Z','/NP','/NFL','/NDL','/R:1','/W:1'
                    )

                    Write-Log "Синхронизация драйвера на ${Computer} через robocopy..." -Level "INFO"
                    [System.Windows.Forms.Application]::DoEvents()
                    $robocopyOutput = & robocopy @robocopyArgs
                    $robocopyExitCode = $LASTEXITCODE
                    [System.Windows.Forms.Application]::DoEvents()

                    $robocopyOutput | Where-Object { $_ } | ForEach-Object {
                        Write-Log "   $_" -Level "INFO"
                    }

                    if ($robocopyExitCode -gt 7) {
                        throw "robocopy завершился с кодом $robocopyExitCode"
                    }

                    Write-Log "Синхронизация драйвера завершена (код robocopy: $robocopyExitCode)" -Level "SUCCESS"

                    if ($localInfHash) {
                        $remoteFileState = Invoke-Command -ComputerName $Computer -ScriptBlock {
                            param($path)
                            if (Test-Path $path) {
                                $hash = Get-FileHash -Path $path -Algorithm 'SHA256'
                                [PSCustomObject]@{ Exists = $true; Hash = $hash.Hash }
                            } else {
                                [PSCustomObject]@{ Exists = $false; Hash = $null }
                            }
                        } -ArgumentList $RemoteInfPath -ErrorAction Stop
                    } else {
                        $remoteFileState = Invoke-Command -ComputerName $Computer -ScriptBlock {
                            param($path)
                            [PSCustomObject]@{ Exists = (Test-Path $path); Hash = $null }
                        } -ArgumentList $RemoteInfPath -ErrorAction Stop
                    }
                }

                if (-not $remoteFileState) {
                    $fileExists = Invoke-Command -ComputerName $Computer -ScriptBlock {
                        param($path)
                        Test-Path $path
                    } -ArgumentList $RemoteInfPath -ErrorAction Stop
                } else {
                    $fileExists = $remoteFileState.Exists
                }

                if (-not $fileExists) {
                    Write-Log "Файл драйвера не найден на ${Computer}: ${RemoteInfPath}" -Level "ERROR"
                    $failedInstalls++
                    $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                    $currentStep += 2
                    Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
                    continue
                }
            } catch {
                Write-Error-Log "Ошибка синхронизации драйвера на ${Computer}" -Exception $_
                $failedInstalls++
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                $currentStep += 2
                Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
                continue
            }

            # Этап 4: Установка драйвера и принтера
            $currentStep++
            Set-OperationStatus "Установка принтера на ${Computer}"
            Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
            
            try {
                # Удалённый скрипт установки
                $RemoteScript = {
                    param($PrinterAddress, $Name, $Driver, $InfPath)
                    
                    $ErrorActionPreference = 'Stop'
                    $PortName = $PrinterAddress
                    
                    # Определяем, является ли адрес IP или именем
                    $isIP = $PrinterAddress -match '^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
                    
                    try {
                        # 1. Создать порт
                        if (-not (Get-PrinterPort -Name $PortName -ErrorAction SilentlyContinue)) {
                            if ($isIP) {
                                # Для IP-адреса используем PrinterHostAddress
                                Add-PrinterPort -Name $PortName -PrinterHostAddress $PrinterAddress
                            } else {
                                # Для имени используем PrinterHostAddress (имя хоста также поддерживается)
                                Add-PrinterPort -Name $PortName -PrinterHostAddress $PrinterAddress
                            }
                            Write-Output "Порт '${PortName}' создан"
                        } else {
                            Write-Output "Порт '${PortName}' уже существует"
                        }
                        
                        # 2. Установить драйвер принтера
                        if (-not (Get-PrinterDriver -Name $Driver -ErrorAction SilentlyContinue)) {
                            Write-Output "Установка драйвера: $InfPath"
                            
                            # Добавляем в хранилище PnP
                            $pnputilOut = pnputil.exe /add-driver "$InfPath" /install /force 2>&1
                            if ($LASTEXITCODE -ne 0) {
                                Write-Output "Предупреждение pnputil: код $LASTEXITCODE"
                                $pnputilOut | ForEach-Object { Write-Output "   $_" }
                            }

                            # Регистрируем как драйвер принтера
                            Add-PrinterDriver -Name $Driver
                            Write-Output "Драйвер принтера '$Driver' добавлен"
                        } else {
                            Write-Output "Драйвер '$Driver' уже установлен"
                        }
                        
                        # 3. Добавить принтер
                        if (-not (Get-Printer -Name $Name -ErrorAction SilentlyContinue)) {
                            Add-Printer -Name $Name -PortName $PortName -DriverName $Driver
                            Write-Output "Принтер '${Name}' добавлен"
                        } else {
                            Write-Output "Принтер '${Name}' уже существует"
                        }
                        
                        Write-Output "Установка завершена успешно"
                        return $true
                        
                    } catch {
                        Write-Output "Ошибка установки: $($_.Exception.Message)"
                        return $false
                    }
                }

                # Выполнить установку принтера
                [System.Windows.Forms.Application]::DoEvents()
                $result = Invoke-Command -ComputerName ${Computer} -ScriptBlock $RemoteScript -ArgumentList $printerIP, $PrinterName, $DriverName, $RemoteInfPath -ErrorAction Stop
                [System.Windows.Forms.Application]::DoEvents()
                
                # Проверяем результат
                $installSuccess = $result[-1] -eq $true
                if ($installSuccess) {
                    Write-Log "Принтер успешно установлен на ${Computer}" -Level "SUCCESS"
                    $successfulInstalls++
                    $successfulComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                } else {
                    Write-Log "Ошибка установки принтера на ${Computer}" -Level "ERROR"
                    $failedInstalls++
                    $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                }
                
                # Логируем детали
                $result[0..($result.Length-2)] | ForEach-Object { Write-Log "   ${_}" -Level "INFO" }
                
            } catch {
                Write-Error-Log "Ошибка установки принтера на ${Computer}" -Exception $_
                $failedInstalls++
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
            }

            # Этап 5: Завершение (уже выполнен в этапе 4)
            $currentStep++
            Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps

            } catch {
                Write-Error-Log "Критическая ошибка обработки ${Computer}" -Exception $_
                $failedInstalls++
                $failedComputers += @{ Computer = $Computer; WindowsVersion = $windowsVersion }
                # Пропускаем оставшиеся этапы при ошибке
                $completedStepsForThisComputer = ($currentStep - 1) % 5
                $remainingStepsForThisComputer = 5 - $completedStepsForThisComputer
                $currentStep += $remainingStepsForThisComputer
                Update-CurrentProgress -CurrentStep $currentStep -TotalSteps $totalSteps
            }
        }

        # Завершение операции
        Write-Log "=== УСТАНОВКА ЗАВЕРШЕНА ===" -Level "INFO"
        Write-Log "Успешно установлено: $successfulInstalls" -Level "SUCCESS"
        Write-Log "Ошибок установки: $failedInstalls" -Level "ERROR"
        
        # Итоговый отчет с именами ПК и версиями Windows
        Write-Log "=== ИТОГОВЫЙ ОТЧЕТ ===" -Level "INFO"
        
        if ($successfulComputers.Count -gt 0) {
            Write-Log "Успешно установлено ($($successfulComputers.Count)):" -Level "SUCCESS"
            foreach ($pc in $successfulComputers) {
                Write-Log "  ✅ $($pc.Computer) ($($pc.WindowsVersion))" -Level "SUCCESS"
            }
        } else {
            Write-Log "Успешно установлено: нет" -Level "INFO"
        }
        
        if ($failedComputers.Count -gt 0) {
            Write-Log "Ошибки установки ($($failedComputers.Count)):" -Level "ERROR"
            foreach ($pc in $failedComputers) {
                Write-Log "  ❌ $($pc.Computer) ($($pc.WindowsVersion))" -Level "ERROR"
            }
    } catch {
        Write-Error-Log "Критическая ошибка в основном обработчике" -Exception $_
        [System.Windows.Forms.MessageBox]::Show("Произошла критическая ошибка: $($_.Exception.Message)", "Ошибка", "OK", "Error")
    } finally {
        # Восстанавливаем состояние кнопок и курсор
        $form.Cursor = [System.Windows.Forms.Cursors]::Default
        $form.UseWaitCursor = $false
        $buttonRun.Enabled = $true
        $buttonCancel.Enabled = $false
        $global:CancelOperation = $false
        [System.Windows.Forms.Application]::DoEvents()
    }
})

# Инициализация
if (Test-Path $global:DriverInfPath) {
    Update-ModelList -InfPath $global:DriverInfPath
} else {
    Write-Log "⚠️  INF файл не найден по пути: $global:DriverInfPath"
    Write-Log "💡 Проверьте доступность сетевого ресурса или измените путь в коде"
}

# Запуск формы
$form.ShowDialog() | Out-Null