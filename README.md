# n8n + Hermes Autonomous Agent Orchestrator

**English** | [Русская версия](#русская-версия)

An n8n-based orchestration system for running autonomous multi-agent development workflows through a Hermes Gateway.

The system separates **workflow logic**, **runtime configuration**, **agent prompts**, **missions**, and **secrets** so that the workflow can be maintained and reused without editing the main orchestration graph every time a model or mission changes.

---

## English

### What this project does

This project turns n8n into an orchestration layer for three logical AI roles:

```text
                         ┌─────────────────────┐
                         │       n8n           │
                         │   AHAWR workflow    │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ Architect / Planner │
                         │ creates a task plan │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │       Worker        │
                         │ executes one task   │
                         │ through Hermes      │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │      Reviewer       │
                         │ checks the result   │
                         └──────────┬──────────┘
                                    │
                            pass / retry / next
```

The main workflow is the **Autonomous Hermes Architect Worker Reviewer (AHAWR)** workflow.

It is designed for long-running tasks where a single LLM call is not enough. Instead of asking one model to solve everything, the workflow:

1. loads a mission and runtime configuration;
2. asks the Architect/Planner to decompose the mission into tasks;
3. takes one task at a time;
4. starts a Worker run through Hermes;
5. polls Hermes until the run finishes or reaches the configured polling limit;
6. sends the Worker result to an independent Reviewer;
7. accepts the task or retries it;
8. moves to the next task after approval;
9. persists execution state so the workflow can recover from interruptions;
10. returns an overall success or failure result.

The workflow therefore behaves more like a small autonomous engineering pipeline than a simple chatbot.

### Components

#### `AHAWR_v11.json`

The main orchestration workflow.

Its responsibilities include:

- loading configuration from n8n Data Tables;
- loading prompts and mission data;
- starting the Architect/Planner;
- parsing the generated task plan;
- selecting the current task;
- starting and monitoring the Worker;
- starting and monitoring the Reviewer;
- handling retries;
- saving and restoring execution state;
- advancing to the next task;
- producing final `approved` or failure output.

The workflow is intentionally stateful. Task execution state is persisted using n8n Data Tables so that long-running asynchronous Hermes jobs do not have to exist only inside one transient execution.

#### `Hermes_Run_Manager_v2.json`

A reusable n8n sub-workflow that wraps Hermes Gateway operations.

It receives normalized runtime parameters such as:

- role;
- run ID;
- session ID;
- input;
- resume input;
- model;
- provider;
- polling interval;
- maximum polling attempts;
- provider retry limit;
- retry delay configuration.

Its purpose is to hide the low-level Hermes HTTP/poll/retry logic from the main AHAWR workflow.

The main workflow can therefore treat Hermes as a reusable execution service.

### Execution model

The normal control flow is:

```text
Mission
   │
   ▼
Load configuration
   │
   ▼
Architect
   │
   ▼
Parse plan
   │
   ├── Task 1
   │      │
   │      ▼
   │    Worker
   │      │
   │      ▼
   │    Reviewer
   │      │
   │      ├── PASS ──────────────► Next task
   │      │
   │      └── NEEDS_CHANGES ─────► Retry task
   │
   ├── Task 2
   │
   └── ...
   │
   ▼
Approved
```

### Asynchronous Hermes execution

Hermes runs are asynchronous.

A Worker or Reviewer is not treated as a single synchronous LLM request. The workflow:

```text
POST /v1/runs
      │
      ▼
receive run_id
      │
      ▼
wait
      │
      ▼
GET /v1/runs/{run_id}
      │
      ├── running → wait and poll again
      │
      ├── completed → process result
      │
      └── error → retry/fail
```

Polling limits and delays are configurable.

This is important for large models and long-running coding tasks where inference may take substantially longer than a normal HTTP request.

### Retry strategy

The system has two separate levels of retry logic.

**Provider retries** handle temporary failures while communicating with the model provider/Hermes.

**Task retries** handle a Reviewer result that says the task needs changes.

A task can therefore be retried without recreating the entire mission plan.

The configured retry delay sequence is stored as JSON, for example:

```text
[15, 30, 60, 120, 300]
```

The Hermes Run Manager accepts the retry-delay configuration and applies it when a retry is required.

### Persistence and recovery

AHAWR stores execution state in n8n Data Tables.

The state is associated with the mission-specific `state_namespace`.

This allows different missions to keep independent state and prevents one mission from accidentally reusing another mission's execution state.

The persisted information can include items such as:

- current task index;
- current task ID;
- task attempts;
- Architect run/session information;
- Worker run/session information;
- Reviewer run/session information;
- last review result;
- task and mission progress.

The important design principle is:

```text
mission → state_namespace → persisted state
```

### Configuration architecture

The project separates data into three logical Data Tables.

#### `hermes_config`

Runtime settings for model/provider selection and execution limits.

Typical fields include:

```text
profile_id
enabled
architect_model
architect_provider
worker_model
worker_provider
reviewer_model
reviewer_provider
max_tasks
max_attempts_per_task
architect_max_polls
worker_max_polls
reviewer_max_polls
architect_poll_seconds
worker_poll_seconds
reviewer_poll_seconds
architect_max_retries
worker_max_retries
reviewer_max_retries
retry_delays_json
```

This means that changing the model or retry policy does not require modifying the orchestration graph.

#### `agent_prompts`

Versioned prompts for the logical agents.

The important roles are:

- Architect / Planner;
- Worker;
- Reviewer.

Prompts are kept outside the main workflow so they can evolve independently from orchestration logic.

#### `missions`

Mission definitions.

A mission contains the business/engineering objective plus rules and acceptance criteria.

A mission also defines its `state_namespace`, which is used for persistent execution state.

Example:

```text
mission_id: simple-readonly-review
state_namespace: test
```

This makes it possible to have multiple independent missions without changing the core workflow.

### Secrets

Secrets should **not** be stored in:

- `hermes_config.csv`;
- `agent_prompts.csv`;
- `missions.csv`;
- workflow source files.

Use n8n Credentials, environment variables, or another secret-management mechanism.

The repository should contain only configuration that is safe to version-control.

Do not commit:

```text
.env
database files
runtime state
API keys
Bearer tokens
passwords
model weights
local logs
```

### Repository files

The repository is expected to contain files similar to:

```text
.
├── AHAWR_v11.json
├── Hermes_Run_Manager_v2.json
├── hermes_config.csv
├── agent_prompts.csv
├── missions.csv
├── docker-compose.yml
├── .gitignore
└── README.md
```

The exact list may change as the project evolves.

### Requirements

The architecture assumes:

- n8n;
- Hermes Gateway;
- an LLM provider accessible through Hermes;
- optionally a local llama.cpp backend;
- n8n Data Tables;
- Docker/host networking configured so n8n can reach Hermes.

A local deployment can use an arrangement such as:

```text
n8n
  │
  ▼
Hermes Gateway
  │
  ▼
llama.cpp / other model provider
```

The exact hostnames, ports, credentials and model names are deployment-specific and should be configured outside the workflow source when possible.

### Installation

#### 1. Start n8n

Start the n8n deployment using your normal Docker Compose setup.

For example:

```powershell
docker compose up -d
```

Verify that n8n is available.

#### 2. Prepare Hermes

Make sure Hermes Gateway is running and reachable from the n8n container.

For a Docker Desktop deployment, the workflow can use a host address such as:

```text
http://host.docker.internal:8642
```

Use the actual address and port of your deployment.

#### 3. Import the Hermes Run Manager

In n8n:

1. Import `Hermes_Run_Manager_v2.json`.
2. Configure the credentials required by your Hermes deployment.
3. Verify that the sub-workflow executes a simple test request.

Do not copy secrets from example files into production.

#### 4. Create the Data Tables

Create these n8n Data Tables:

```text
hermes_config
agent_prompts
missions
```

Import the corresponding CSV data.

At minimum, there should be:

- an enabled `default` configuration profile;
- enabled agent prompts;
- at least one enabled mission.

#### 5. Check mission state namespace

Every mission used by AHAWR must contain a non-empty:

```text
state_namespace
```

For example:

```text
simple-readonly-review
test
```

The namespace is used as the key for mission state persistence.

#### 6. Import AHAWR

Import:

```text
AHAWR_v11.json
```

Make sure the Execute Workflow nodes point to the imported `Hermes_Run_Manager_v2` workflow.

If n8n assigns a different workflow ID after import, update the references accordingly.

#### 7. Configure credentials

Configure Hermes/API credentials in n8n Credentials or environment variables.

Do not place real credentials into Git-tracked JSON or CSV files.

#### 8. Run a simple mission first

Start with a small read-only mission before using the system against a real coding repository.

A good first mission should:

- have a very small scope;
- avoid destructive commands;
- produce a short result;
- be easy to verify manually.

Only after the full Architect → Worker → Reviewer loop works should you move to larger development missions.

### How to use it

The normal usage pattern is:

```text
1. Add/edit a mission in `missions`
2. Choose models/providers in `hermes_config`
3. Update prompts in `agent_prompts` when necessary
4. Start AHAWR
5. Monitor the n8n execution
6. Inspect Worker and Reviewer results
7. Review the final `approved` or failure output
```

For example, changing the Worker model should normally require changing:

```text
hermes_config.worker_model
hermes_config.worker_provider
```

rather than editing dozens of nodes in the workflow.

Changing the mission should normally require changing the row in:

```text
missions
```

rather than changing the orchestration logic.

### Recommended operational workflow

For development:

```text
Git working tree
      │
      ▼
edit workflow / CSV
      │
      ▼
test in n8n
      │
      ▼
inspect execution
      │
      ▼
git diff
      │
      ▼
commit
```

Keep the workflow JSON, prompt definitions, and non-secret configuration under version control.

Keep runtime data, credentials, local model files, and temporary state outside Git.

### Troubleshooting

#### Mission has no `state_namespace`

Verify that the selected mission row contains:

```text
state_namespace
```

and that the value is not empty.

Also verify that the `Mission`/`Load Mission` nodes actually pass that field into the workflow context.

#### Hermes run never finishes

Check:

- Hermes Gateway health;
- network connectivity from the n8n container;
- model availability;
- polling interval;
- maximum polling count;
- provider errors.

#### Reviewer keeps requesting changes

Inspect:

- Worker output;
- Reviewer prompt;
- task acceptance criteria;
- verification requirements;
- task retry limit.

A Reviewer failure does not necessarily mean the infrastructure is broken; it can be a legitimate task-level rejection.

#### State from another mission is being reused

Check the mission's:

```text
state_namespace
```

It must be unique for independent missions.

### Design principles

The project intentionally follows these principles:

**Orchestration is separate from content.**  
The workflow controls execution; Data Tables hold configuration, prompts and missions.

**Secrets are separate from configuration.**  
Credentials are managed by n8n rather than Git-tracked files.

**Asynchronous execution is explicit.**  
Long-running model calls are handled through run IDs and polling.

**Tasks are independently reviewable.**  
The Reviewer evaluates the current task instead of judging the whole mission at once.

**State is persistent.**  
Long-running work survives transient workflow execution boundaries.

**Missions are data-driven.**  
Changing the mission should not require rewriting the orchestrator.

---

## Русская версия

### Что делает проект

Это система оркестрации автономных AI-агентов на базе **n8n + Hermes Gateway**.

Главный workflow — **Autonomous Hermes Architect Worker Reviewer (AHAWR)**.

Он разделяет работу на три логические роли:

```text
Architect / Planner
        │
        ▼
      Worker
        │
        ▼
     Reviewer
        │
   ┌────┴────┐
   │         │
 PASS    NEEDS_CHANGES
   │         │
   ▼         └──► повтор текущей задачи
следующая
 задача
```

Вместо одного большого запроса модель сначала строит план, затем Worker выполняет задачи по одной, а Reviewer независимо проверяет результат.

### Что происходит при запуске

AHAWR:

1. загружает mission;
2. загружает конфигурацию моделей и параметров;
3. загружает prompts;
4. передаёт миссию Architect/Planner;
5. получает структурированный план задач;
6. выбирает текущую задачу;
7. запускает Worker через Hermes;
8. ждёт завершения асинхронного запуска;
9. при необходимости делает повторные polling-запросы;
10. передаёт результат Worker в Reviewer;
11. получает `pass` или `needs_changes`;
12. либо переходит к следующей задаче, либо повторяет текущую;
13. сохраняет состояние выполнения;
14. после завершения всех задач формирует итоговый результат.

То есть n8n здесь выступает именно как **оркестратор**, а Hermes — как execution gateway для AI-run'ов.

### `AHAWR_v11.json`

Это основной workflow проекта.

Он отвечает за:

- загрузку конфигурации;
- загрузку prompts;
- загрузку mission;
- запуск Architect;
- разбор плана;
- выбор текущей задачи;
- запуск Worker;
- polling состояния Worker;
- запуск Reviewer;
- polling Reviewer;
- retry;
- сохранение состояния;
- восстановление состояния;
- переход между задачами;
- финальный результат.

### `Hermes_Run_Manager_v2.json`

Это переиспользуемый sub-workflow для работы с Hermes Gateway.

Он принимает параметры:

```text
role
run_id
session_id
input
resume_input
model
provider
poll_seconds
max_polls
max_retries
retry_count
retry_delays
```

и инкапсулирует низкоуровневую логику запуска Hermes, ожидания результата, polling и retry.

Благодаря этому основной AHAWR workflow не должен содержать всю HTTP-логику Hermes непосредственно в каждой роли.

### Архитектура

```text
                    n8n
                     │
             ┌───────┴────────┐
             │     AHAWR      │
             └───────┬────────┘
                     │
             ┌───────▼────────┐
             │ Architect       │
             │ планирование    │
             └───────┬────────┘
                     │
             ┌───────▼────────┐
             │ Worker          │
             │ выполнение      │
             └───────┬────────┘
                     │
                Hermes Gateway
                     │
             ┌───────▼────────┐
             │ Reviewer        │
             │ проверка        │
             └───────┬────────┘
                     │
              pass / retry
```

### Асинхронная работа Hermes

Worker и Reviewer не считаются одним синхронным HTTP-запросом.

Логика выглядит так:

```text
POST /v1/runs
      │
      ▼
    run_id
      │
      ▼
    wait
      │
      ▼
GET /v1/runs/{run_id}
      │
      ├── running → повторить polling
      │
      ├── completed → обработать результат
      │
      └── error → retry / failure
```

Это позволяет работать с большими локальными моделями и длительными задачами.

### Retry

В системе существуют два разных уровня retry.

**Provider retry** — повтор связи с provider/Hermes при временной ошибке.

**Task retry** — повтор конкретной задачи, если Reviewer вернул `needs_changes`.

Таким образом, неудача одной задачи не требует заново строить весь план.

Задержки retry хранятся как JSON, например:

```text
[15,30,60,120,300]
```

### Сохранение состояния

AHAWR хранит состояние выполнения в n8n Data Tables.

Ключом состояния является mission-specific:

```text
state_namespace
```

Например:

```text
mission_id: simple-readonly-review
state_namespace: test
```

Это позволяет различным миссиям иметь независимое состояние.

В сохранённом состоянии могут находиться:

- текущая задача;
- индекс задачи;
- количество попыток;
- run/session Architect;
- run/session Worker;
- run/session Reviewer;
- результат последнего review;
- прогресс mission.

### Data Tables

Проект использует три основные таблицы.

#### `hermes_config`

Хранит runtime-конфигурацию:

```text
profile_id
enabled
architect_model
architect_provider
worker_model
worker_provider
reviewer_model
reviewer_provider
max_tasks
max_attempts_per_task
architect_max_polls
worker_max_polls
reviewer_max_polls
architect_poll_seconds
worker_poll_seconds
reviewer_poll_seconds
architect_max_retries
worker_max_retries
reviewer_max_retries
retry_delays_json
```

Изменение модели или параметров выполнения обычно не требует изменения workflow.

#### `agent_prompts`

Хранит prompts для:

```text
Architect / Planner
Worker
Reviewer
```

Prompts отделены от workflow и могут изменяться независимо.

#### `missions`

Хранит миссии:

- `mission_id`;
- цель;
- правила;
- acceptance criteria;
- `state_namespace`.

Поэтому можно создавать различные сценарии работы без переписывания AHAWR.

### Где хранить секреты

Секреты **не должны** находиться в:

```text
hermes_config.csv
agent_prompts.csv
missions.csv
AHAWR_v11.json
Hermes_Run_Manager_v2.json
```

Используйте:

- n8n Credentials;
- environment variables;
- внешний secret manager.

В Git не должны попадать:

```text
.env
*.sqlite
API keys
Bearer tokens
passwords
model weights
runtime data
logs
```

### Файлы репозитория

Типичная структура:

```text
.
├── AHAWR_v11.json
├── Hermes_Run_Manager_v2.json
├── hermes_config.csv
├── agent_prompts.csv
├── missions.csv
├── docker-compose.yml
├── .gitignore
└── README.md
```

### Установка

#### 1. Запустить n8n

Например:

```powershell
docker compose up -d
```

#### 2. Запустить Hermes Gateway

Hermes должен быть доступен из контейнера n8n.

Для Docker Desktop возможен адрес:

```text
http://host.docker.internal:8642
```

Используйте фактический адрес вашей установки.

#### 3. Импортировать `Hermes_Run_Manager_v2.json`

После импорта:

- настроить credentials;
- проверить доступ к Hermes;
- выполнить тестовый запуск.

#### 4. Создать Data Tables

Создайте:

```text
hermes_config
agent_prompts
missions
```

и импортируйте соответствующие CSV.

Должны существовать:

- enabled `default` configuration;
- enabled prompts;
- минимум одна enabled mission.

#### 5. Проверить `state_namespace`

У выбранной mission должен присутствовать непустой:

```text
state_namespace
```

Например:

```text
test
```

#### 6. Импортировать AHAWR

Импортируйте:

```text
AHAWR_v11.json
```

После импорта проверьте Execute Workflow nodes и убедитесь, что они вызывают именно импортированный `Hermes_Run_Manager_v2`.

### Первый запуск

Первую проверку рекомендуется делать на простой read-only mission.

Пример:

```text
Прочитать указанный файл.
Ничего не изменять.
Не выполнять команды, изменяющие файлы.
Вернуть краткое резюме.
```

Сначала нужно убедиться, что полностью работает цепочка:

```text
Mission
  ↓
Architect
  ↓
Worker
  ↓
Reviewer
  ↓
Approved
```

И только после этого использовать систему для реальных coding-задач.

### Как пользоваться

Обычный рабочий цикл:

```text
1. Добавить или изменить mission
2. Выбрать модели в hermes_config
3. При необходимости изменить prompts
4. Запустить AHAWR
5. Следить за execution в n8n
6. Проверить Worker result
7. Проверить Reviewer result
8. Получить финальный approved/failure результат
```

Для смены Worker-модели достаточно изменить:

```text
worker_model
worker_provider
```

в `hermes_config`.

Для смены задачи достаточно изменить соответствующую запись в `missions`.

### Принципы проекта

**Workflow отвечает за orchestration.**  
Данные и prompts вынесены отдельно.

**Credentials отделены от Git.**  
Секреты должны находиться в n8n Credentials или другом secret storage.

**Длительные AI-запуски асинхронные.**  
Используются `run_id`, polling и retry.

**Каждая задача проверяется отдельно.**  
Reviewer принимает решение по текущей задаче.

**Состояние сохраняется.**  
Долгие запуски не зависят только от одной execution-сессии.

**Mission является data-driven.**  
Изменение миссии не требует переписывать оркестратор.

---

## License

Add the license that matches your intended distribution model.
