**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

/api/user?search=Test 

б) Выведем все группы исполнителей, у которых в названии содержится подстрока «Тестовая», и вывод представим только 2мя полями для каждой группы – «Название» и «Список исполнителей» 

/api/taskexecutorgroup?fields=Name,ExecutorIds&search=Тестовая 

_**page** ,_ _**pagesize** – параметры отображения таблицы_ 

_Пример вывода в xml_ 

GET /api/taskexecutorgroup?serviceid=16 

_<ExecutorGroupList> <ExecutorGroups> <ExecutorGroup> <Id>1</Id> <Name>Группа 1</Name> <Description/> <ExecutorIds>45, 43</ExecutorIds> <Executors>admin2, Администратор</Executors> </ExecutorGroup> <ExecutorGroup> <Id>2</Id> <Name>Группа 2</Name> <Description/> <ExecutorIds>45, 44</ExecutorIds> <Executors>admin2, test 1</Executors> </ExecutorGroup> </ExecutorGroups> <Paginator> <Count>2</Count> <Page>1</Page> <PageCount>1</PageCount> <PageSize>10</PageSize> <CountOnPage>2</CountOnPage> </Paginator> </ExecutorGroupList>_ 

**Тип заявки** 

/api/tasktype 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 59 из 82 

