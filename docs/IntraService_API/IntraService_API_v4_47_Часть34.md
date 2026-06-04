**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_**inactive** – позволяет отфильтровать список сервисов по признаку «Актуальный» (задается на вкладке «Главная»)_ 

_Возможные значения_ 

- **false** – получить только актуальные сервисы (значение **по умолчанию** , если параметр не задан); 

- **true** - получить как актуальные сервисы, так и неактуальные. 

_**for** –параметр для фильтрации списка сервисов_ 

_Возможные значения:_ 

- **Параметр не задан** – вернет список сервисов, если у текущего пользователя есть права на просмотр списка сервисов; 

- **filtertasks** – возвращает список сервисов, на которые назначен пользователь (вместе с родительскими) 

- **createtask** - возвращает список сервисов, на которые назначен пользователь (вместе с родительскими). Для каждого сервиса в выводе присутствуют поля _CanCreateTask_ и _HasTaskTypes_ (описание можно посмотреть в таблице) 

_**page** ,_ _**pagesize** – параметры отображения таблицы_ 

## _Пример вывода в xml_ 

## 1) GET: /api/service?fields=Id,Name,Code 

**==> picture [217 x 252] intentionally omitted <==**

**----- Start of picture text -----**<br>
<ServiceList><br><Services><br><Service><br><Id>16</Id><br><Code>с2</Code><br><Name>сервис 2</Name><br></Service><br><Service><br><Id>15</Id><br><Code>с1</Code><br><Name>Сервис1</Name><br></Service><br></Services><br><Paginator><br><Count>2</Count><br><Page>1</Page><br><PageCount>1</PageCount><br><PageSize>25</PageSize><br><CountOnPage>2</CountOnPage><br></Paginator><br></ServiceList><br>**----- End of picture text -----**<br>


2) GET: /api/service?for=createtask&fields=Id,Name,Code 

_<ServiceList> <Services> <Service>_ 

_<Id>16</Id>_ 

_<Code>с2</Code> <Name>сервис 2</Name> <CanCreateTask>True</CanCreateTask>_ 

_<HasTaskTypes>True</HasTaskTypes>_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 34 из 82 

