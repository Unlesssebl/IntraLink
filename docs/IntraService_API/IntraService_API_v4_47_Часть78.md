**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_<Paginator> <Count>10</Count> <Page>1</Page> <PageCount>5</PageCount> <PageSize>2</PageSize> <CountOnPage>2</CountOnPage> </Paginator> </AssetTypeList>_ 

Получение определенного типа актива 

_Вызов_ 

GET /api/assettype/{assettypeid} 

Где **{assettypeid}** – идентификатор типа актива 

_Вывод дополнительного поля актива AssetTypeFieldView в списке AssetTypeFields_ 

Поля AssetTypeFieldView 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Id|Int|Идентификатор дополнительного поля|
|FieldTypeId|Int|Тип дополнительного поля:<br>1 – строка, 2 – число, 3 – дата, 4 – чекбокс, 5 –<br>выпадающий список, 6 – файл, 7 – текст, 8 –<br>пустой интервал,9 – произвольный html-элемент|
|Hint|String|Подсказка|
|Name|String|Название|
|Unit|String|Единица измерения|
|IsRequired|Bool|Обязательность поля|
|FieldSelectValues|List of string|Список значений для типа дополнительного поля<br>«Выпадающий список»(5)|



_Пример вывода в xml_ GET: /api/assettype/16 

_<AssetTypeView>_ 

_<AssetTypeFields> <AssetTypeFieldView> <FieldSelectValues>_ 

_<d4p1:string>один</d4p1:string> <d4p1:string> два</d4p1:string> <d4p1:string> три</d4p1:string>_ 

_</FieldSelectValues>_ 

_<FieldTypeId>5</FieldTypeId> <Hint i:nil="true"/> <Id>55</Id> <IsRequired>false</IsRequired> <Name>Тест список</Name> <Unit i:nil="true"/> </AssetTypeFieldView> <AssetTypeFieldView> <FieldSelectValues i:nil="true"/>_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 78 из 82 

