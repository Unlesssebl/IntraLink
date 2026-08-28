**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## _Пример вывода в xml_ 

На сервисе 16 присутствует тип заявки 1005. Тип заявки имеет 2 дополнительных поля: «строка» и «выпадающий список» с 3мя значениями, включающими иерархию: 

GET: /api/tasktype/1005?serviceid=16 

_<TaskTypeView>_ 

_<Description/> <Id>1005</Id> <Image16Url>http://localhost/IntraService4.40p2/img/tasktypes/blue.jpg </Image16Url> <IsArchive>false</IsArchive>_ 

_<Name>type 1</Name> <TaskTypeFields> <TaskTypeFieldView> <DefaultValue>тест значение</DefaultValue> <FieldTypeId>1</FieldTypeId> <Hint/> <Id>1014</Id> <IsRequired>false</IsRequired> <Name>Строка</Name> <Rights>6</Rights> <TaskTypeComboBoxes i:nil="true"/> <Unit/> </TaskTypeFieldView> <TaskTypeFieldView> <DefaultValue i:nil="true"/> <FieldTypeId>5</FieldTypeId> <Hint/> <Id>1018</Id> <IsRequired>false</IsRequired> <Name>Список</Name> <Rights>6</Rights> <TaskTypeComboBoxes> <TaskTypeComboBoxView> <Id>17</Id> <Name>1</Name> <ParentId i:nil="true"/> <Path>17|</Path> </TaskTypeComboBoxView> <TaskTypeComboBoxView> <Id>19</Id> <Name>1.1</Name> <ParentId>17</ParentId> <Path>17|19|</Path> </TaskTypeComboBoxView> <TaskTypeComboBoxView> <Id>18</Id> <Name>2</Name> <ParentId i:nil="true"/> <Path>18|</Path> </TaskTypeComboBoxView> </TaskTypeComboBoxes> <Unit/> </TaskTypeFieldView>_ 

> **ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 63 из 82 

