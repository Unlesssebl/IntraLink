**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_</Status>_ 

_<Status> <Id>29</Id> <Name>Выполнена</Name> <Description/> <Image16Url>http://localhost/IntraService4.40/img/statuses/fixed16.png </Image16Url> <Image24Url>http://localhost/IntraService4.40/img/statuses/fixed24.png </Image24Url> <IsCommentRequired>False</IsCommentRequired> <IsFinal>False</IsFinal> <IsFixed>True</IsFixed> <IsDeadlineAccountingPaused>True</IsDeadlineAccountingPaused> <IsConcurrence>False</IsConcurrence> <IsExternal>False</IsExternal> </Status> </Statuses> <Paginator> <Count>2</Count><Page>1</Page><PageCount>1</PageCount><PageSize>25</PageSize><Coun tOnPage>2</CountOnPage>_ 

_</Paginator>_ 

_</TaskLifetimeList>_ 

## **Трудозатраты** 

## api/taskexpenses 

**Метод GET. Получение трудозатрат по заявке и определенных трудозатрат** 

Поля трудозатрат для получения 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Id|Int|Идентификатортрудозатраты|
|UserId|Int|Идентификатор исполнителя, за которого списана<br>трудозатрата|
|UserName|String|Имя исполнителя,за которого списана трудозатрата|
|Date|DateTime|Дата списания трудозатрат|
|Minutes|Int|Количество минут|
|Rate|Decimal|Рейт|
|Comments|String|Комментарий|
|TaskId|Int|Номерзаявки,к которой относятся трудозатраты|
|EditorId|Int|Идентификатор пользователя, создавшего или<br>изменившего последним трудозатрату|
|EditorName|String|Имя пользователя, создавшего или изменившего<br>последним трудозатрату|



Получение всех трудозатрат для заявки. 

## _Вызов:_ 

GET: api/taskexpenses?taskid={taskid}&fields={fieldList}&pagesize={value}&page={value} 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 66 из 82 

