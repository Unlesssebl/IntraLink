**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_<Login>admin</Login> <MobilePhone i:nil="true"/> <Name>Администратор</Name> <Phone i:nil="true"/> <Position i:nil="true"/> <RoleId>37</RoleId> <RoleType>1</RoleType> <UtcOffset>+03:00</UtcOffset> </CurrenUserInfo>_ 

## **Метод POST. Создание пользователя** 

## Поля для создания пользователя 

|Название|Тип|Обязательность|Описание|
|---|---|---|---|
|RoleId|Int|+|Идентификаторсистемнойроли|
|CompanyId|Int|+|Идентификаторкомпании|
|Login|String|+|Логин пользователя в системе|
|Name|String|+|Имя|
|Password|String|+|Пароль.|
|ConfirmPassword|String|+|Подтверждение пароля.|
|Email|String||Email|
|Phone|String||Телефон|
|Comments|String||Описание|
|IsArchive|Bool||Архивный|
|Position|String||Должность|
|MobilePhone|String||Мобильный|
|TimeZone|String||Часовой пояс|
|CurrentLanguage|String||Язык|



## Вызов и пример использования 

POST: /api/user 

В теле запроса передается объект User. Допускается передавать только те поля, которые содержат значения. Обязательно должны быть заполнены поля, отмеченные в таблице как обязательные. Запрос возвращает все поля созданного пользователя (аналогично запросу Получение определенного пользователя). 

## **Пример:** 

Создадим пользователя с ролью 2, компанией 10, именем «Иванов Андрей», логином «ivanov», e- mail’ом «ivanov@mail.ru» и паролем «test123»: 

POST: /api/user 

_{“RoleId”: 2, “CompanyId”:10, “Login”:”ivanov”, “Name ”:”Иванов Андрей”,_ 

_“Password”:”test123”,” ConfirmPassword”:”test123”,”Email”:”ivanov@mail.ru”}_ 

**Метод PUT. Изменение пользователя** 

## Поля для изменения пользователя 

Поля пользователя для изменения аналогичны, как и при создании (указаны в таблице). 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 57 из 82 

