**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_Пример вывода в xml:_ 

_<UserView> <ADGuid i:nil="true"/> <Changed>2015-11-16T12:30:03.443</Changed> <Comments i:nil="true"/> <CompanyId>91</CompanyId> <CompanyName>Подразделение 1</CompanyName> <CompanyPath i:nil="true"/> <CurrentLanguage i:nil="true"/> <Email>nr@intravision.ru</Email> <Id>44</Id> <IsArchive>false</IsArchive> <Login>test1</Login> <MobilePhone i:nil="true"/> <Name>test 1</Name> <Phone i:nil="true"/> <Position i:nil="true"/> <RoleId>37</RoleId> <TimeZone>Russian Standard Time</TimeZone> <UtcOffset>+03:00</UtcOffset> </UserView>_ 

Получение информации по текущему пользователю 

_Вызов_ 

GET /api/user?getcurrentuserinfo=true 

Запрос вернет следующие поля для текущего пользователя: _Language_ - текущий язык _UtcOffset_ - числовое значение смещения от мирового времени (UTC) _Id_ – идентификатор пользователя _Login_ – логин пользователя _Email_ – емейл пользователя _Name_ – имя пользователя _RoleId_ – идентификатор системной роли пользователя _RoleType_ – тип системной роли пользователя (1 – Исполнитель, 2 - Клиент) _CompanyId_ – идентификатор компании пользователя _IsArchive_ – признак архивности пользователя _DefaultTaskFilterId_ – идентификатор фильтра по умолчанию для списка заявок А также поля – _Phone_ , _MobilePhone_ , _Position_ , _Comments_ – Телефон, Мобильный телефон, Должность, Описание 

_Пример вывода в xml_ 

_<CurrenUserInfo> <Comments i:nil="true"/> <CompanyId>30</CompanyId> <DefaultTaskFilterId>106</DefaultTaskFilterId> <Email>test@test.ru</Email> <Id>1</Id> <IsArchive>false</IsArchive> <Language>ru</Language>_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 56 из 82 

