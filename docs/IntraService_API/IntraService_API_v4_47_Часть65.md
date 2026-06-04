**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_Возможные параметры_ 

_**taskid** – обязательный параметр_ 

номер заявки Пример: Введем жизненный цикл заявки № 255 GET: /api/tasklifetime?taskid=255 

_**include** – необязательный параметр._ 

Позволяет добавлять связанные ресурсы. 

_Возможные значения:_ 

- **PRIORITY** (приоритет); 

- **SERVICE** (сервис); 

- **STATUS** (статус) 

_**lastcommentsontop** – необязательный параметр._ 

Позволяет отсортировать жизненный цикл в порядке «Последний комментарий вверху списка» 

_Возможные значения:_ 

**true** или **false** 

_Пример вывода в xml_ 

GET: /api/tasklifetime?taskid=151&include=status 

_<TaskLifetimeList> <TaskLifetimes> <TaskLifetime> <Date>12.11.2015 13:44:53</Date> <EditorId>43</EditorId> <Editor>Администратор</Editor> <StatusId>29</StatusId> </TaskLifetime> <TaskLifetime> <Date>11.11.2015 15:24:10</Date> <EditorId>43</EditorId> <Editor>Администратор</Editor> <StatusId>31</StatusId>_ 

_</TaskLifetime> </TaskLifetimes> <Statuses> <Status> <Id>31</Id> <Name>Открыта</Name> <Description/> <Image16Url>http://localhost/IntraService4.40/img/statuses/actual16.png </Image16Url> <Image24Url>http://localhost/IntraService4.40/img/statuses/actual24.png </Image24Url> <IsCommentRequired>False</IsCommentRequired> <IsFinal>False</IsFinal> <IsFixed>False</IsFixed> <IsDeadlineAccountingPaused>False</IsDeadlineAccountingPaused> <IsConcurrence>False</IsConcurrence> <IsExternal>False</IsExternal>_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 65 из 82 

