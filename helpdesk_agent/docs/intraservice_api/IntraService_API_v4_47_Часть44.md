**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_Пример вывода в xml: <CompanyView> <Id>101</Id> <Name>Test company</Name> <FullName>Test company</FullName> <Domain/> <ServiceDeskPath>http://mycompany.intraservice.ru</ ServiceDeskPath > <IsArchive>False</IsArchive> <ParentId/> <Path>101|</Path> <ADGuid/> <CurrentLanguage/> <TimeZone/> <TypeId>3</TypeId> <Address/> <WEB>test web</WEB> <Note/> <Phone/> <Email/> <ContactPersonId/> <ManagerId>37</ManagerId> <Changed>29.09.2015 11:12:27</Changed> </CompanyView>_ 

## **Метод POST. Создание новой компании** 

## Поля для создания компании 

|**Поле**|**Тип значения**|**Обязательность**|**Комментарий**|
|---|---|---|---|
|TypeId|Целое число (int)|+|Идентификатор типа компании|
|Name|string|+|Название компании|
|ServiceDeskPath|Строка (string)|+|Путь к системе (для почтовых<br>уведомлений). Если не<br>передается, по умолчанию<br>возьмется путь из Общих<br>настроек.|
|Domain|Строка (string)||Домены компании (через<br>запятую)|
|ParentId|int||Идентификатор родительской<br>компании|
|IsArchive|bool||Признак архивный|
|TimeZone|string||Часовой пояс|
|Address|string||Адрес|
|Email|string||Email|
|Note|string||Примечание|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 44 из 82 

