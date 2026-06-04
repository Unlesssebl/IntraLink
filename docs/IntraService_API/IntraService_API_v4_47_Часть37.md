**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## **Фильтр** 

## /api/filter 

**Метод GET. Получение списка фильтров** 

Поля фильтра для получения 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Id|Int|Идентификатор фильтра|
|Name|String|Название|
|IsCommon|Bool|Общийфильтр (да\нет)|
|IsDefault|Bool|Фильтрпо-умолчанию(да\нет)|



## Получение списка фильтров 

_Вызов_ 

GET: = /api/filter?resource { resource _name} 

## _Возможные параметры_ 

## **resource** - обязательный параметр 

Cодержит название ресурса, по которому возвращается список фильтров (task, user, service и т.п.). 

**Пример:** Получим список фильтров для заявок 

GET: /api/filter?resource=task 

_Пример вывода в xml_ 

GET: /api/filter?resource=task 

_<ArrayOfFilterView> <FilterView> <Id>106</Id> <IsCommon>false</IsCommon> <IsDefault>true</IsDefault> <Name>1. Инциденты</Name>_ 

_</FilterView> <FilterView> <Id>107</Id> <IsCommon>false</IsCommon> <IsDefault>false</IsDefault> <Name>2. Проблемы</Name> </FilterView> <ArrayOfFilterView>_ 

**Статус** 

/api/taskstatus 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 37 из 82 

