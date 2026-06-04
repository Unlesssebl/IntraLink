**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

Выведем информацию по следующим настройкам: «максимально допустимый размер файла» и «Отправлять уведомления инициатору» 

GET: /api/settings?keys=maxfilesize,EditorTaskNotificationEvent 

_Пример вывода в xml_ 

_<ArrayOfSettingsView> <SettingsView> <Description i:nil="true"/> <FieldTypeId>2</FieldTypeId> <Key>maxfilesize</Key> <Name>Максимально допустимый размер файла, в КБ</Name> <Value>7000</Value> </SettingsView> <SettingsView> <Description i:nil="true"/> <FieldTypeId>3</FieldTypeId> <Key>EditorTaskNotificationEvent</Key> <Name>Отправлять уведомления инициатору</Name> <Value>True</Value> </SettingsView> </ArrayOfSettingsView>_ 

## **Токен устройства пользователя** 

/api/token 

**Метод POST. Добавление токена текущему пользователю** 

## Поля для создания токена 

|**Название**|**Тип**<br>**данных**|**Обязательность**|**Описание**|
|---|---|---|---|
|Token|string|+|Токен устройства, полученный с сервера<br>Apple или Google|
|DeviceType|string|+|Тип устройства, строка “iOS” или<br>“Android”|
|DeviceName|String||Название устройства. Отображается в<br>интерфейсе настроек пользователя|



## Вызов и пример использования 

POST: /api/token 

## **Пример:** 

Добавим для текущего пользователя токен устройства Android « _dfkjDJFHd84948hdkd7hdjfkdf_ » c именем «Мой телефон»: 

_Json : {“_ Token _”: “dfkjDJFHd84948hdkd7hdjfkdf”, “_ DeviceType _”:”Android”, “_ DeviceName _”: ”Мой телефон ”}_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 80 из 82 

