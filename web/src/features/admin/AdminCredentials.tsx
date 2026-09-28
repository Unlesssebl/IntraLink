import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  KeyRound,
  LockKeyhole,
  RefreshCw,
  ServerCog,
  ShieldCheck,
  UserRound,
} from "lucide-react";

import { adminApi } from "@/shared/api";
import { Badge, Button, Card, Input, useToast } from "@/shared/ui";

const statusKey = ["admin", "service-credentials"] as const;

export const AdminCredentials: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [syncCatalog, setSyncCatalog] = useState(true);

  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: ({ signal }) => adminApi.getServiceCredentialsStatus(signal),
    retry: false,
  });

  useEffect(() => {
    if (statusQuery.data?.login && !login) setLogin(statusQuery.data.login);
  }, [statusQuery.data?.login, login]);

  const saveMutation = useMutation({
    mutationFn: () => adminApi.updateServiceCredentials(login.trim(), password, syncCatalog),
    onSuccess: (response) => {
      setPassword("");
      queryClient.setQueryData(statusKey, response.status);
      if (response.catalog_sync === "failed") {
        toast.info(response.catalog_sync_error || "Креды сохранены, но каталог не синхронизирован");
      } else {
        toast.success(
          response.catalog_sync === "succeeded"
            ? "Креды сохранены, каталог синхронизирован"
            : "Креды сервисного бота сохранены"
        );
      }
    },
    onError: (error: Error) => toast.error(error.message || "Не удалось сохранить учетные данные"),
  });

  const status = statusQuery.data;
  const canSubmit = login.trim().length > 0 && password.length > 0 && !saveMutation.isPending;

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.16em] text-neutral-500">
            <ShieldCheck className="h-3.5 w-3.5" />
            Administration / Credentials vault
          </div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight text-neutral-100">
            Сервисный аккаунт IntraService
          </h1>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-neutral-400">
            Эти данные использует worker для фонового опроса заявок и синхронизации каталога сервисов.
          </p>
        </div>
        <Badge
          variant={status?.configured ? "success" : statusQuery.isError ? "danger" : "warning"}
          dot
          pulse={statusQuery.isFetching}
          pill
        >
          {statusQuery.isFetching
            ? "Проверка"
            : status?.configured
              ? "Настроено"
              : statusQuery.isError
                ? "Недоступно"
                : "Не настроено"}
        </Badge>
      </div>

      {statusQuery.isError && (
        <div className="rounded-lg border border-rose-800/60 bg-rose-950/30 px-4 py-3 text-xs text-rose-300">
          {(statusQuery.error as Error).message}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[1.45fr_0.8fr]">
        <Card
          title="Учетные данные"
          subtitle="Перед сохранением пара логин/пароль проверяется запросом к IntraService"
          action={<LockKeyhole className="h-4 w-4 text-neutral-500" />}
        >
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (canSubmit) saveMutation.mutate();
            }}
          >
            <Input
              label="Логин сервисного бота"
              value={login}
              onChange={(event) => setLogin(event.target.value)}
              placeholder="service.bot"
              autoComplete="username"
              icon={<UserRound className="h-3.5 w-3.5" />}
            />

            <div className="relative">
              <Input
                label="Пароль"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Введите новый пароль"
                autoComplete="new-password"
                icon={<KeyRound className="h-3.5 w-3.5" />}
                className="pr-10"
              />
              <button
                type="button"
                aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                onClick={() => setShowPassword((value) => !value)}
                className="absolute right-2.5 top-[25px] rounded p-1 text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200"
              >
                {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>

            <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-neutral-800 bg-[#0e1013] p-3">
              <input
                type="checkbox"
                checked={syncCatalog}
                onChange={(event) => setSyncCatalog(event.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 accent-neutral-200"
              />
              <span>
                <span className="block text-xs font-medium text-neutral-200">Синхронизировать каталог после сохранения</span>
                <span className="mt-0.5 block text-[11px] leading-4 text-neutral-500">
                  Обновит список сервисов и устранит причину catalog_unavailable, если каталог пройдет проверку.
                </span>
              </span>
            </label>

            <div className="flex items-center justify-between border-t border-neutral-800/80 pt-4">
              <span className="flex items-center gap-1.5 text-[10px] text-neutral-500">
                <ShieldCheck className="h-3.5 w-3.5" />
                Пароль не отображается и не возвращается через API
              </span>
              <Button
                type="submit"
                variant="primary"
                loading={saveMutation.isPending}
                disabled={!canSubmit}
                icon={<CheckCircle2 className="h-3.5 w-3.5" />}
              >
                Проверить и сохранить
              </Button>
            </div>
          </form>
        </Card>

        <div className="space-y-5">
          <Card
            title="Состояние vault"
            action={
              <Button
                size="sm"
                variant="ghost"
                aria-label="Обновить состояние"
                onClick={() => statusQuery.refetch()}
                loading={statusQuery.isFetching}
                icon={<RefreshCw className="h-3.5 w-3.5" />}
              >
                Обновить
              </Button>
            }
          >
            <dl className="space-y-3 text-xs">
              <StatusRow
                label="PostgreSQL"
                value={
                  !status?.encryption_ready
                    ? "Нет ключа шифрования"
                    : status.configured
                      ? "Зашифровано"
                      : "Нет данных"
                }
                ok={!!status?.configured && !!status?.encryption_ready}
              />
              <StatusRow label="Redis cache" value={status?.redis_cached ? "Прогрет" : "Пуст"} ok={!!status?.redis_cached} />
              <StatusRow label="Логин" value={status?.login || "Не указан"} mono />
              <StatusRow
                label="Обновлено"
                value={status?.updated_at ? new Date(status.updated_at).toLocaleString("ru-RU") : "Нет данных"}
              />
            </dl>
          </Card>

          <Card title="Каталог сервисов" action={<Database className="h-4 w-4 text-neutral-500" />}>
            <dl className="space-y-3 text-xs">
              <StatusRow
                label="Состояние"
                value={status?.catalog.available ? `Версия ${status.catalog.version}` : "Недоступен"}
                ok={!!status?.catalog.available}
              />
              <StatusRow label="Сервисы" value={String(status?.catalog.entries ?? 0)} mono />
              <StatusRow label="Активные привязки" value={String(status?.catalog.active_bindings ?? 0)} mono />
            </dl>
          </Card>
        </div>
      </div>

      <div className="flex items-start gap-3 rounded-lg border border-neutral-800/80 bg-[#0e1013] px-4 py-3">
        <ServerCog className="mt-0.5 h-4 w-4 shrink-0 text-neutral-500" />
        <p className="text-[11px] leading-5 text-neutral-400">
          Секрет хранится как зашифрованный Basic-токен в PostgreSQL и кэшируется в Redis. Смена пароля выполняется
          повторным сохранением этой формы; открытого значения в интерфейсе нет.
        </p>
      </div>
    </div>
  );
};

const StatusRow: React.FC<{ label: string; value: string; ok?: boolean; mono?: boolean }> = ({
  label,
  value,
  ok,
  mono,
}) => (
  <div className="flex items-center justify-between gap-4 border-b border-neutral-800/60 pb-2.5 last:border-0 last:pb-0">
    <dt className="text-neutral-500">{label}</dt>
    <dd className={`flex items-center gap-1.5 text-right text-neutral-200 ${mono ? "font-mono" : ""}`}>
      {ok !== undefined && <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />}
      {value}
    </dd>
  </div>
);
