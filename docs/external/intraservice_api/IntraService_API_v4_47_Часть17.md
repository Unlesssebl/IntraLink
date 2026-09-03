**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_}_ 

2) Пример вывода в xml GET: /api/task?fields=Id,Name,ServiceId&include=service&pagesize=1&page=2 _<TaskList>_ 

_<Tasks> <Task> <Id>158</Id> <Name>test category</Name> <ServiceId>16</ServiceId> </Task> </Tasks> <Services> <Service> <Id>16</Id> <Code>с2</Code> <Name>сервис 2</Name> <Description/> <IsArchive>False</IsArchive> <IsPublic>False</IsPublic> <ParentId/> <Path>16|</Path> </Service> </Services> <Paginator> <Count>11</Count> <Page>2</Page> <PageCount>11</PageCount> <PageSize>1</PageSize> <CountOnPage>1</CountOnPage> </Paginator> </TaskList>_ 

**Фильтрация списка заявок по полям** 

Поля для фильтрации 

|**Поле фильтрации**|**Тип передаваемого**<br>**значения**|**Описание фильтрации**|
|---|---|---|
|ChangedLessThan|DateTime|Дата последнего изменения заявок<br>меньше илиравнауказанной|
|ChangedMoreThan|DateTime|Дата последнего изменения заявок<br>больше илиравнауказанной|
|Changed|DateTime|Дата последнего изменения заявок равна<br>указанному дню (время не учитывается,<br>т.е. с 00:00до 23:59указанногодня)|
|CreatedLessThan|DateTime|Дата создания заявок меньше или равна<br>указанной|
|CreatedMoreThan|DateTime|Дата создания заявок больше или равна<br>указанной|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 17 из 82 

