**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

- отсутствует, то возвращается коллекция ресурсов; 

- присутствует, то в ответе возвращается конкретный ресурс с указанным id. 

Для PUT запросов достаточно отсылать только те поля, которые изменяются. 

## **Поддерживаемые форматы** 

Для получения данных (GET) можно указать формат: **json** или **xml** . Используемый формат определяется по стандартным параметрам в заголовке запроса. При отправке запросов, _для передачи данных_ (POST, PUT) используется формат _**json**_ . 

## **Ответ на запрос и возврат ошибки** 

В ответе на запрос возвращается стандартный код состояния HTTP. Коды 2XX соответствуют успешному выполнению запроса, коды 4XX-5XX – ошибка. 

При выполнении POST и PUT запросов в результате успешного выполнения в большинстве случаев возвращается также созданный\измененный объект, по аналогии с вызовом получения определенного ресурса (метод GET с параметром id). 

## Пример **успешно выполненного** запроса: 

Получим сервис с идентификатором 1: 

GET /api/service/1 Ответ в json: _Accept: application/json Status: 200 OK { "Id": 1, "Code": "SRVC", "Name": "Service", "Description": "Описание сервиса", "IsArchive": false, "IsPublic": false, "ParentId": 185, "Path": "185|1|" }_ 

Все запросы, завершившиеся **неудачей** , возвращают помимо ответа Bad Request также и jsonобъект с указанием ошибки. 

Пример такого ответа для PUT-запроса: 

_{"Message":"The request is invalid.","_ 

_MessageDetail":"The parameters dictionary contains a null entry for parameter 'id' of non-nullable type 'System.Int32' for method 'IntraService.API.Domain.Models.Views.UserView_ 

_Get(IntraService.Security.IIntraServicePrincipal, Int32)' in 'IntraService.Web.API.Controllers.UserController'. An optional parameter must be a reference type, a nullable type, or be declared as an optional parameter."}_ 

## **Ограничение числа получаемых полей** 

Для запросов конкретных ресурсов и их коллекций может быть ограничено число получаемых полей ресурса. Для этого используется параметр **fields** , в котором через запятую перечисляются 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 8 из 82 

