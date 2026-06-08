**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

|ReactionDate|DateTime||Время реакции|
|---|---|---|---|
|ReactionDateFact|DateTime||Фактическое время реакции|
|ReactionOverdue|Bool||Срок реакции истек?|
|Closed|Date||Дата закрытия|
|TypeId|Int||Идентификатор типа|
|Type|String||Тип|
|EvaluationId|Int||Идентификатор оценки заявки|
|Evaluation|String||Оценка заявки (название)|
|IsMassIncident|Bool||Массовый инцидент?|
|CrеatorIP|String||Ip заявителя|
|ExecutorGroupId|Int||Идентификатор группы<br>исполнителей|
|ExecutorGroup|String||Название группы<br>исполнителей|
|TaskRepeatRuleId|Int||Идентификатор правила<br>повторения|
|ResolutionDateFact|DateTime||Фактическое время<br>выполнения|
|ResolutionOverdue|Bool||Срок выполнения истек?|
|Coordinators|String|+|Список согласующих|
|IsCoordinatedForCoordinators|String|+|Перечисление признаков<br>согласования (True, False) для<br>списка согласующих. Порядок<br>соответствует порядку в<br>Coordinators и CoordinatorIds.|
|CoordinatorIds|String||Идентификаторы<br>согласующих|
|Observers|String|+|Список наблюдателей|
|ObserverIds|String||Идентификаторы<br>наблюдателей|
|Data|xml|+|Значение дополнительных<br>полей заявки в виде xml|
|||||
||_Конкретная заявка_|||
|||||
|Field{id}|String||Значение дополнительного<br>поля с идентификатором id.<br>Например,Field8.|
|CompletionStatus|Int||Выполнено, %|
|WorkflowId|Int||Идентификатор бизнес<br>процесса|
|IsClient|Boolean||Тип роли пользователя на<br>сервисе заявки: true – клиент,<br>false - исполнитель|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 12 из 82 

