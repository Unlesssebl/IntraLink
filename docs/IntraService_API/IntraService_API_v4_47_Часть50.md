**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## Получение определенного документа компании 

_Вызов:_ 

GET:  api/companydocument/{id} 

## Где _{id}_ – идентификатор документа компании 

_Пример вывода в xml:_ 

_<CompanyDocumentView> <Id>2</Id> <FileName>Документ_договора.docx</FileName> <NameFromUser>Договор на обслуживание</NameFromUser> <CompanyId>93</CompanyId> <CompanyFullName>Тестовый клиент</CompanyFullName> <IsActive>True</IsActive> <TypeId>2</TypeId> <TypeName>Договор</TypeName> <FileId>3</FileId> <DateBegin>01.11.2017 19:00:00</DateBegin> <DateEnd>01.11.2018 19:00:00</DateEnd> <EditorId>43</EditorId> <EditorName>Администратор</EditorName> <CreatorId>43</CreatorId> <CreatorName>Администратор</CreatorName> <Created>24.11.2017 13:14:09</Created> <Changed>24.11.2017 13:14:09</Changed> </CompanyDocumentView>_ 

## **Метод POST. Создание нового документа компании** 

## Поля для создания документа 

|**Поле**|**Тип значения**|**Обязательность**|**Комментарий**|
|---|---|---|---|
|TypeId|int|+|Типа документа:<br>1 – другое,<br>2 - договор|
|CompanyId|int|+|Идентификатор компании, к<br>которой относится документ.|
|FileId|Int|+|Идентификатор файла документа.<br>Для загрузки файла и получения<br>его идентификатора используйте|
|DateBegin|DateTime|+/-|Начало действия договора. Поле<br>**обязательно**, если тип договора<br>TypeId равен 2 – «договор»|
|DateEnd|DateTime|+/-|Окончание действия договора.<br>Поле**обязательно**, если тип<br>договора TypeId равен 2 –<br>«договор»|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 50 из 82 

