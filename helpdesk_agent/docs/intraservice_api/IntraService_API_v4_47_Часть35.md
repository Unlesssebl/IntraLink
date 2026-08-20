**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_</Service> <Service> <Id>15</Id> <Code>с1</Code> <Name>Сервис1</Name> <CanCreateTask>True</CanCreateTask> <HasTaskTypes>True</HasTaskTypes> </Service> </Services> <Paginator> <Count>2</Count> <Page>1</Page> <PageCount>1</PageCount> <PageSize>25</PageSize> <CountOnPage>2</CountOnPage> </Paginator> </ServiceList>_ 

## Получение определенного сервиса 

_Вызов_ 

GET: /api/service/serviced Где **serviceid** – идентификатор сервиса 

_Пример вывода в xml_ 

GET: /api/service/15 

_<ServiceView> <Code>с1</Code> <Description>сс</Description> <Id>15</Id> <IsArchive>false</IsArchive> <IsPublic>false</IsPublic> <Name>Сервис1</Name> <ParentId i:nil="true"/> <Path>15|</Path> </ServiceView>_ 

**Метод POST. Назначение пользователей на сервис** api/serviceuser 

Поля для назначения пользователей на сервис 

|**Поле**|**Тип значения**|**Обязательность**<br>**Комментарий**|**Обязательность**<br>**Комментарий**|
|---|---|---|---|
|ServiceId|Целое число (int)|+|Идентификатор сервиса|
|AddUsers|ServiceUserView<br>Array|+|Массив значений Пользователь-<br>Роль (ServiceUserView), которых<br>назначаем на сервис|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 35 из 82 

