import React, { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  Building2,
  CheckCircle2,
  Cpu,
  Database,
  Eye,
  EyeOff,
  GitBranch,
  KeyRound,
  LockKeyhole,
  Network,
  Power,
  RefreshCw,
  ServerCog,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  UserRound,
  Wrench,
} from "lucide-react";

import { adminApi, type CapabilityExecutionPolicyStatusDTO } from "@/shared/api";
import { Badge, Button, Card, Input, Modal, Textarea, useToast } from "@/shared/ui";

const statusKey = ["admin", "service-credentials"] as const;
const adStatusKey = ["admin", "ad-credentials"] as const;
const bindingKey = ["admin", "ad-account-binding"] as const;
const executionPoliciesKey = ["admin", "execution-policies"] as const;

const REASON_LABELS: Record<string, string> = {
  policy_disabled: "Политика выполнения отключена администратором",
  policy_not_found: "Политика выполнения не найдена в базе данных",
  deployment_force_disabled: "Мутирующие операции принудительно отключены на сервере (MUTATING_AUTOMATION_FORCE_DISABLED)",
  circuit_breaker_open: "Предохранитель (circuit breaker) открыт из-за предыдущих сбоев",
  worker_unavailable: "Отсутствует активный воркер для выполнения действия",
  no_compatible_worker: "Отсутствует активный совместимый воркер для выполнения действия",
  stale_worker_heartbeat: "Связь с воркером потеряна (heartbeat timeout > 30с)",
  worker_engine_version_mismatch: "Версия движка политик на воркере не совпадает с API",
  worker_deployment_force_disabled: "Воркер запущен с флагом принудительного запрета мутаций",
  missing_service_binding: "Нет активной валидированной привязки сервиса в каталоге",
  unsupported_capability_in_binding: "Возможность не разрешена в привязке сервиса",
  invalid_service_binding: "Привязка сервиса не прошла валидацию",
  preflight_not_passed: "Предварительные проверки не пройдены",
  preflight_expired: "Срок действия предварительных проверок истек",
};


export const AdminCredentials: React.FC = () => {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [syncCatalog, setSyncCatalog] = useState(true);
  const [bindingConfirmed, setBindingConfirmed] = useState(false);
  const [adServers, setAdServers] = useState("");
  const [adUsername, setAdUsername] = useState("");
  const [adPassword, setAdPassword] = useState("");
  const [showAdPassword, setShowAdPassword] = useState(false);
  const [adPort, setAdPort] = useState("636");

  const statusQuery = useQuery({
    queryKey: statusKey,
    queryFn: ({ signal }) => adminApi.getServiceCredentialsStatus(signal),
    retry: false,
  });
  const bindingQuery = useQuery({
    queryKey: bindingKey,
    queryFn: ({ signal }) => adminApi.getAdAccountBinding(signal),
    retry: false,
  });
  const adStatusQuery = useQuery({
    queryKey: adStatusKey,
    queryFn: ({ signal }) => adminApi.getAdCredentialsStatus(signal),
    retry: false,
  });
  const bindingMutation = useMutation({
    mutationFn: () => {
      const binding = bindingQuery.data;
      if (!binding?.catalog_hash || !binding.service_path) throw new Error("Каталог или сервис #53 недоступен");
      return adminApi.activateAdAccountBinding(binding.catalog_hash, binding.service_path);
    },
    onSuccess: (binding) => {
      setBindingConfirmed(false);
      queryClient.setQueryData(bindingKey, binding);
      toast.success("Привязка создания учётной записи активирована");
    },
    onError: (error: Error) => toast.error(error.message || "Не удалось активировать привязку"),
  });

  const [selectedPolicy, setSelectedPolicy] = useState<CapabilityExecutionPolicyStatusDTO | null>(null);
  const [editDesiredState, setEditDesiredState] = useState<"enabled" | "disabled">("disabled");
  const [editReason, setEditReason] = useState("");

  const policiesQuery = useQuery({
    queryKey: executionPoliciesKey,
    queryFn: ({ signal }) => adminApi.getExecutionPolicies(signal),
    retry: false,
    refetchInterval: 10000,
  });

  const updatePolicyMutation = useMutation({
    mutationFn: () => {
      if (!selectedPolicy) throw new Error("Политика не выбрана");
      return adminApi.updateExecutionPolicy(
        selectedPolicy.capability_key,
        editDesiredState,
        selectedPolicy.revision,
        editReason.trim()
      );
    },
    onSuccess: (updated) => {
      setSelectedPolicy(null);
      setEditReason("");
      queryClient.setQueryData<CapabilityExecutionPolicyStatusDTO[]>(executionPoliciesKey, (old) =>
        old ? old.map((p) => (p.capability_key === updated.capability_key ? updated : p)) : [updated]
      );
      toast.success(`Политика для «${updated.name}» успешно обновлена`);
    },
    onError: (error: Error) => {
      toast.error(error.message || "Не удалось обновить политику");
      policiesQuery.refetch();
    },
  });

  const openEditPolicy = (policy: CapabilityExecutionPolicyStatusDTO) => {
    setSelectedPolicy(policy);
    setEditDesiredState(policy.desired_state === "enabled" ? "disabled" : "enabled");
    setEditReason("");
  };

  useEffect(() => {
    if (statusQuery.data?.login && !login) setLogin(statusQuery.data.login);
  }, [statusQuery.data?.login, login]);

  useEffect(() => {
    const status = adStatusQuery.data;
    if (!status) return;
    setAdServers(status.servers.join("\n"));
    setAdUsername(status.username || "");
    setAdPort(String(status.port));
  }, [adStatusQuery.data?.updated_at]);

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

  const saveAdMutation = useMutation({
    mutationFn: () => {
      const servers = adServers.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean);
      return adminApi.updateAdCredentials(servers, adUsername.trim(), adPassword, Number(adPort), true);
    },
    onSuccess: (response) => {
      setAdPassword("");
      queryClient.setQueryData(adStatusKey, response.status);
      toast.success("Подключение к Active Directory проверено и сохранено");
    },
    onError: (error: Error) => toast.error(error.message || "Не удалось проверить подключение к AD"),
  });

  const status = statusQuery.data;
  const canSubmit = login.trim().length > 0 && password.length > 0 && !saveMutation.isPending;
  const parsedAdPort = Number(adPort);
  const canSubmitAd =
    adServers.trim().length > 0 &&
    adUsername.trim().length > 0 &&
    adPassword.length > 0 &&
    Number.isInteger(parsedAdPort) &&
    parsedAdPort > 0 &&
    parsedAdPort <= 65535 &&
    !saveAdMutation.isPending;

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.16em] text-neutral-500">
            <ShieldCheck className="h-3.5 w-3.5" />
            Administration / Credentials vault
          </div>
          <h1 className="mt-2 text-xl font-semibold tracking-tight text-neutral-100">
            Интеграции и доступы
          </h1>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-neutral-400">
            Зашифрованные учётные данные IntraService и Active Directory для управляемых workflow.
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

      <Card
        title="Подключение Active Directory"
        subtitle="Домен и корневой контейнер зафиксированы; пароль сохраняется только после read-only проверки LDAP"
        action={
          <Badge
            variant={adStatusQuery.data?.configured ? "success" : adStatusQuery.isError ? "danger" : "warning"}
            dot
            pulse={adStatusQuery.isFetching}
          >
            {adStatusQuery.isFetching
              ? "Проверка"
              : adStatusQuery.data?.configured
                ? "Настроено"
                : "Не настроено"}
          </Badge>
        }
      >
        <div className="grid gap-5 lg:grid-cols-[1.35fr_0.85fr]">
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (canSubmitAd) saveAdMutation.mutate();
            }}
          >
            <Textarea
              label="Контроллеры домена"
              value={adServers}
              onChange={(event) => setAdServers(event.target.value)}
              placeholder={"dc1.corporate.loc\ndc2.corporate.loc"}
              rows={3}
              className="font-mono text-xs"
            />
            <div className="grid gap-4 sm:grid-cols-[1fr_120px]">
              <Input
                label="Доменная учётная запись"
                value={adUsername}
                onChange={(event) => setAdUsername(event.target.value)}
                placeholder="svc.intralink@corporate.loc"
                autoComplete="username"
                icon={<UserRound className="h-3.5 w-3.5" />}
              />
              <Input
                label="Порт"
                type="number"
                min={1}
                max={65535}
                value={adPort}
                onChange={(event) => setAdPort(event.target.value)}
                className="font-mono"
              />
            </div>
            <div className="relative">
              <Input
                label="Пароль доменной учётной записи"
                type={showAdPassword ? "text" : "password"}
                value={adPassword}
                onChange={(event) => setAdPassword(event.target.value)}
                placeholder="Введите пароль для проверки и сохранения"
                autoComplete="new-password"
                icon={<KeyRound className="h-3.5 w-3.5" />}
                className="pr-10"
              />
              <button
                type="button"
                aria-label={showAdPassword ? "Скрыть пароль AD" : "Показать пароль AD"}
                onClick={() => setShowAdPassword((value) => !value)}
                className="absolute right-2.5 top-[25px] rounded p-1 text-neutral-500 hover:bg-neutral-800 hover:text-neutral-200"
              >
                {showAdPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
            <label className="flex cursor-pointer items-start gap-2.5 rounded-md border border-neutral-800 bg-[#0e1013] p-3">
              <input
                type="checkbox"
                checked
                readOnly
                disabled
                className="mt-0.5 h-3.5 w-3.5 accent-neutral-200 disabled:opacity-80"
              />
              <span>
                <span className="block text-xs font-medium text-neutral-200">Использовать LDAPS</span>
                <span className="mt-0.5 block text-[11px] leading-4 text-neutral-500">
                  Для установки пароля нового пользователя требуется защищённое LDAP-соединение; рекомендуемый порт — 636.
                </span>
              </span>
            </label>
            <div className="flex items-center justify-between border-t border-neutral-800/80 pt-4">
              <span className="flex items-center gap-1.5 text-[10px] text-neutral-500">
                <LockKeyhole className="h-3.5 w-3.5" />
                Открытый пароль не возвращается через API
              </span>
              <Button
                type="submit"
                variant="primary"
                loading={saveAdMutation.isPending}
                disabled={!canSubmitAd}
                icon={<Network className="h-3.5 w-3.5" />}
              >
                Проверить и сохранить
              </Button>
            </div>
          </form>

          <div className="space-y-3 rounded-md border border-neutral-800 bg-[#0e1013] p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-medium text-neutral-200">
                <Building2 className="h-4 w-4 text-neutral-500" />
                Контур размещения
              </div>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Обновить состояние AD"
                onClick={() => adStatusQuery.refetch()}
                loading={adStatusQuery.isFetching}
                icon={<RefreshCw className="h-3.5 w-3.5" />}
              >
                Обновить
              </Button>
            </div>
            {adStatusQuery.isError && (
              <div className="rounded border border-rose-800/60 bg-rose-950/30 px-3 py-2 text-[11px] text-rose-300">
                {(adStatusQuery.error as Error).message}
              </div>
            )}
            <dl className="space-y-3 text-xs">
              <StatusRow label="Домен" value={adStatusQuery.data?.domain || "corporate.loc"} mono />
              <StatusRow
                label="Root OU"
                value={adStatusQuery.data?.users_root_ou || "OU=CORPORATE_USERS,DC=corporate,DC=loc"}
                mono
              />
              <StatusRow
                label="Vault"
                value={
                  !adStatusQuery.data?.encryption_ready
                    ? "Нет ключа шифрования"
                    : adStatusQuery.data?.configured
                      ? "Зашифровано"
                      : "Нет данных"
                }
                ok={!!adStatusQuery.data?.configured && !!adStatusQuery.data?.encryption_ready}
              />
              <StatusRow label="Проверенный DC" value={adStatusQuery.data?.verified_server || "Нет данных"} mono />
              <StatusRow
                label="Найдено OU"
                value={adStatusQuery.data?.ou_count == null ? "Нет данных" : String(adStatusQuery.data.ou_count)}
                mono
              />
              <StatusRow
                label="Последняя проверка"
                value={
                  adStatusQuery.data?.last_verified_at
                    ? new Date(adStatusQuery.data.last_verified_at).toLocaleString("ru-RU")
                    : "Нет данных"
                }
              />
            </dl>
          </div>
        </div>
      </Card>

      {/* Карточка 1: Привязка сервиса */}
      <Card
        title="1. Привязка сервиса (Service Route Binding)"
        subtitle="Авторизация маршрута в каталоге услуг IntraService: сервис #53 → employee_onboarding → create_ad_user"
        action={
          <Badge variant={bindingQuery.data?.active ? "success" : "warning"} dot>
            {bindingQuery.data?.active ? "Маршрут активен" : "Требует подтверждения"}
          </Badge>
        }
      >
        <div className="space-y-4 text-xs">
          <div className="rounded-md border border-blue-900/40 bg-blue-950/20 p-3 text-[11px] text-blue-200 flex items-start gap-2.5">
            <GitBranch className="h-4 w-4 shrink-0 text-blue-400 mt-0.5" />
            <div>
              <span className="font-semibold text-neutral-100">Принцип архитектурного разделения:</span> Зелёный статус привязки подтверждает корректность маршрута услуги в каталоге IntraService, но <strong className="text-amber-300 font-semibold">НЕ является разрешением на исполнение</strong>. Мутации в AD требуют включенной политики capability и активного воркера.
            </div>
          </div>

          <div className="rounded-md border border-neutral-800 bg-[#0e1013] p-3">
            <div className="text-[10px] uppercase tracking-wider text-neutral-500">Полный путь сервиса</div>
            <div className="mt-1 text-neutral-100">{bindingQuery.data?.service_path || "Сервис #53 отсутствует в активном каталоге"}</div>
            <div className="mt-2 font-mono text-[10px] text-neutral-500 break-all">catalog {bindingQuery.data?.catalog_hash || "—"}</div>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <StatusRow label="Workflow" value={bindingQuery.data?.workflow_key || "employee_onboarding_workflow"} mono />
            <StatusRow label="Capability" value={bindingQuery.data?.capability_key || "create_ad_user"} mono />
            <StatusRow label="Task type" value={bindingQuery.data?.task_type_id == null ? "Не задан" : String(bindingQuery.data.task_type_id)} />
            <StatusRow label="Обязательные факты" value={(bindingQuery.data?.required_facts || []).join(", ")} />
          </div>
          {!bindingQuery.data?.active && (
            <div className="flex flex-col gap-3 border-t border-neutral-800 pt-3 sm:flex-row sm:items-center sm:justify-between">
              <label className="flex items-start gap-2 text-neutral-300">
                <input type="checkbox" checked={bindingConfirmed} onChange={(event) => setBindingConfirmed(event.target.checked)} className="mt-0.5 accent-neutral-200" />
                <span>Я проверил полный путь сервиса и подтверждаю привязку к #53.</span>
              </label>
              <Button
                variant="primary"
                loading={bindingMutation.isPending}
                disabled={!bindingConfirmed || !bindingQuery.data?.service_path || !bindingQuery.data?.catalog_hash}
                onClick={() => bindingMutation.mutate()}
              >
                Активировать привязку
              </Button>
            </div>
          )}
        </div>
      </Card>

      {/* Карточка 2: Политика выполнения capability */}
      <Card
        title="2. Политика выполнения capability"
        subtitle="Административный допуск к исполнению мутирующих операций в инфраструктуре"
        action={
          <Button
            size="sm"
            variant="ghost"
            aria-label="Обновить политики"
            onClick={() => policiesQuery.refetch()}
            loading={policiesQuery.isFetching}
            icon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            Обновить
          </Button>
        }
      >
        <div className="space-y-3 text-xs">
          {policiesQuery.isError && (
            <div className="rounded border border-rose-800/60 bg-rose-950/30 px-3 py-2 text-[11px] text-rose-300">
              {(policiesQuery.error as Error).message}
            </div>
          )}
          {policiesQuery.data && policiesQuery.data.length === 0 && (
            <p className="text-neutral-500 py-2">Нет зарегистрированных мутирующих возможностей.</p>
          )}
          <div className="divide-y divide-neutral-800/80">
            {policiesQuery.data?.map((pol) => (
              <div key={pol.capability_key} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 first:pt-0 last:pb-0">
                <div className="space-y-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-neutral-100">{pol.name}</span>
                    <Badge variant={pol.desired_state === "enabled" ? "success" : "neutral"} dot>
                      {pol.desired_state === "enabled" ? "Включено" : "Отключено"} (rev: {pol.revision})
                    </Badge>
                    <span className="font-mono text-[10px] text-neutral-500">{pol.capability_key}</span>
                  </div>
                  <p className="text-neutral-400 text-[11px] leading-4">{pol.description}</p>
                  <div className="text-[10px] text-neutral-500 flex flex-wrap items-center gap-x-2">
                    <span>Изменил: <strong className="text-neutral-300">{pol.changed_by}</strong></span>
                    <span>•</span>
                    <span>{new Date(pol.changed_at).toLocaleString("ru-RU")}</span>
                    <span>•</span>
                    <span>Причина: «{pol.reason}»</span>
                  </div>
                </div>
                <div className="shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => openEditPolicy(pol)}
                    icon={<Sliders className="h-3.5 w-3.5" />}
                  >
                    Изменить политику
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </Card>

      {/* Карточка 3: Состояние исполнителя */}
      <Card
        title="3. Состояние исполнителя"
        subtitle="Мониторинг Attestation воркеров (TTL 30с), предохранителей (Circuit Breaker) и потолка развертывания"
        action={<Cpu className="h-4 w-4 text-neutral-500" />}
      >
        <div className="space-y-3 text-xs">
          <div className="divide-y divide-neutral-800/80">
            {policiesQuery.data?.map((pol) => (
              <div key={`runtime-${pol.capability_key}`} className="py-3 space-y-2 first:pt-0 last:pb-0">
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-neutral-200">{pol.name}</span>
                  <span className="font-mono text-[10px] text-neutral-500">{pol.capability_key}</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-3">
                  <StatusRow
                    label="Воркер (Attestation)"
                    value={pol.worker_available ? "Активен (OK, v2.0.0)" : "Недоступен / Timeout"}
                    ok={pol.worker_available}
                  />
                  <StatusRow
                    label="Предохранитель"
                    value={pol.circuit_state === "closed" ? "Закрыт (исправен)" : "Открыт (сбои)"}
                    ok={pol.circuit_state === "closed"}
                  />
                  <StatusRow
                    label="Deployment Ceiling"
                    value={pol.deployment_ceiling ? "Разрешено" : "MUTATING_AUTOMATION_FORCE_DISABLED"}
                    ok={pol.deployment_ceiling}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </Card>

      {/* Карточка 4: Эффективный допуск */}
      <Card
        title="4. Эффективный допуск"
        subtitle="Итоговое конъюнктивное решение Execution Policy Evaluator для мутирующих возможностей"
        action={<ShieldAlert className="h-4 w-4 text-neutral-500" />}
      >
        <div className="space-y-3 text-xs">
          <div className="divide-y divide-neutral-800/80">
            {policiesQuery.data?.map((pol) => {
              const isAllowed = pol.effective_state === "allowed";
              return (
                <div key={`effective-${pol.capability_key}`} className="py-3 space-y-2 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-neutral-200">{pol.name}</span>
                    <Badge variant={isAllowed ? "success" : "danger"} dot>
                      {isAllowed ? "Разрешено к выполнению" : "Выполнение заблокировано"}
                    </Badge>
                  </div>
                  {isAllowed ? (
                    <div className="flex items-center gap-2 text-emerald-400 text-[11px] bg-emerald-950/20 border border-emerald-900/40 rounded p-2">
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                      <span>Все условия соблюдены: политика включена, воркер доступен, маршрут подтвержден.</span>
                    </div>
                  ) : (
                    <div className="space-y-1 bg-amber-950/20 border border-amber-900/40 rounded p-2.5">
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        Блокирующие причины:
                      </div>
                      <ul className="space-y-1 pt-1">
                        {pol.reason_codes.map((code) => (
                          <li key={code} className="text-[11px] text-amber-200 flex items-start gap-1.5">
                            <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-amber-400" />
                            <span>{REASON_LABELS[code] || `Код причины: ${code}`}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </Card>

      {/* Модальное окно изменения политики */}
      <Modal
        isOpen={selectedPolicy !== null}
        onClose={() => setSelectedPolicy(null)}
        title={`Изменение политики: ${selectedPolicy?.name}`}
        description={`Управление административным допуском к выполнению capability «${selectedPolicy?.capability_key}».`}
      >
        <div className="space-y-4 text-xs">
          <div className="space-y-2">
            <label className="text-neutral-300 font-medium block">Целевое состояние политики</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer text-neutral-200">
                <input
                  type="radio"
                  name="desiredState"
                  value="enabled"
                  checked={editDesiredState === "enabled"}
                  onChange={() => setEditDesiredState("enabled")}
                  className="accent-emerald-500"
                />
                <span className="font-semibold text-emerald-400">Включено (enabled)</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer text-neutral-200">
                <input
                  type="radio"
                  name="desiredState"
                  value="disabled"
                  checked={editDesiredState === "disabled"}
                  onChange={() => setEditDesiredState("disabled")}
                  className="accent-rose-500"
                />
                <span className="font-semibold text-rose-400">Отключено (disabled)</span>
              </label>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-neutral-300 font-medium block">
              Причина изменения <span className="text-neutral-500 font-normal">(обязательно, от 10 до 500 символов)</span>
            </label>
            <textarea
              value={editReason}
              onChange={(e) => setEditReason(e.target.value)}
              placeholder="Укажите обоснование (например: Плановое включение автоматизации онбординга сотрудников...)"
              className="w-full h-24 rounded-md border border-neutral-800 bg-[#0e1013] p-2.5 text-neutral-100 placeholder:text-neutral-600 focus:outline-none focus:border-neutral-600"
              maxLength={500}
            />
            <div className="flex justify-between text-[10px] text-neutral-500">
              <span>Символов: {editReason.trim().length} / 500 (минимум 10)</span>
              <span>Ожидаемая ревизия: {selectedPolicy?.revision}</span>
            </div>
          </div>

          <div className="flex items-center justify-end gap-2 border-t border-neutral-800 pt-3">
            <Button variant="ghost" onClick={() => setSelectedPolicy(null)}>
              Отмена
            </Button>
            <Button
              variant="primary"
              loading={updatePolicyMutation.isPending}
              disabled={editReason.trim().length < 10 || editReason.trim().length > 500}
              onClick={() => updatePolicyMutation.mutate()}
            >
              Сохранить политику
            </Button>
          </div>
        </div>
      </Modal>
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

