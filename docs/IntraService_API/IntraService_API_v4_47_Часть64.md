**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_</TaskTypeFields>_ 

_</TaskTypeView>_ 

## **Жизненный цикл заявки** 

## /api/tasklifetime 

**Метод GET. Получение событий жизненного цикла для заявки** 

## Поля жизненного цикла для получения 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Date|DateTime|Дата изменения|
|EditorId|Int|Id пользователя,изменившего заявку|
|Editor|String|Имя пользователя,изменившего заявку|
|StatusId|Int|Id статуса|
|Comments|String|Комментарий|
|IsPublic|Boolean|Комментарийдоступен клиенту|
|Executors|String|Список имен исполнителей через запятую|
|ExecutorsGroup|String|Группа исполнителей|
|Creator|String|Заявитель|
|Participants|String|Список имен наблюдателей через запятую|
|Name|String|Название|
|Description|String|Описание|
|Categories|String|Имена категорий через запятую|
|PriorityId|Int|Id приоритета|
|Files|String|Список**всех**имен и id файлов, разделенных «|»,<br>через запятую. Показывается в первой записи и в<br>записи, в которой имеет место быть изменение<br>спискафайлов.|
|RemovedFiles|String|Список удаленных файлов в виде<br>«<fileid>|<filename>» через запятую. Присутствует<br>в первой записи и только если были удалены<br>какие-либофайлы.|
|AddedFiles|String|Список добавленных файлов в виде<br>«<fileid>|<filename>» через запятую. Присутствует<br>в первой записи и только если были добавлены<br>какие-либофайлы.|
|ServiceId|Int|Id сервиса|
|Deadline|DateTime|Срок исполнения|



## Получение жизненного цикла заявки 

## _Вызов_ 

## GET: 

= = = /api/tasklifetime?taskid {task_id}&include {resource_name_list}&lastcommentsontop {value}&pagesiz e={value}&page={value} 

Список отсортирован в соответствии с настройками в системе либо в соответствии с необязательным параметром _lastcommentsontop_ . 

> **ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 64 из 82 

