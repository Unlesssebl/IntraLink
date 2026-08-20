**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## Получение списка документов 

_Вызов:_ 

GET: = = = /api/companydocument?fields {fieldList}&search {searchString}&{filterFields}&pagesize {value}&page = {value} 

_Возможные параметры:_ 

**fields** - перечень полей ресурса «Документ компании» для вывода(из таблицы) 

## **Например:** 

Выведем все названия файлов докментов и их типы списком 

= /api/companydocument?fields FileName,TypeId 

_**search –** строка поиска. Ищет данную строку как подстроку в полях, указанных в таблице в соответствующей колонке._ 

## **Например:** 

а) Выведем все документы, у которых в одном из полей для поиска содержится подстрока «Test»: 

/api/companydocument?search=Test 

б) Выведем все документы, у которых в одном из полей для поиска содержится подстрока 

«Test», и вывод представим только 2мя полями для каждого документа – «Название файла» и «Тип документа» 

/api/companydocument?fields=FileName,TypeId&search=Test 

_**page** ,_ _**pagesize** – параметры отображения таблицы_ 

_**{ filterFields }** поля для фильтрации_ 

Все условия фильтрации склеиваются по условию И. Подробнее в разделе о фильтрации документов компаний 

_Пример вывода в xml_ 

GET: /api/companydocument 

_<CompanyDocumentList> <Documents> <Document> <Id>2</Id> <FileName>Документ_договора.docx</FileName> <NameFromUser>Договор на обслуживание</NameFromUser> <IsActive>True</IsActive> <CompanyId>93</CompanyId> <CompanyFullName>Тестовый клиент</CompanyFullName> <CreatorId>43</CreatorId> <CreatorName>Администратор</CreatorName> <EditorId>43</EditorId> <EditorName>Администратор</EditorName> <FileId>3</FileId> <TypeId>2</TypeId> <TypeName>Договор</TypeName> <Changed>24.11.2017 13:14:09</Changed> <Created>24.11.2017 13:14:09</Created> <DateBegin>01.11.2017 19:00:00</DateBegin> <DateEnd>01.11.2018 19:00:00</DateEnd>_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 47 из 82 

