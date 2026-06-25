# Процесс разработки НеНойBot

## Brain Storm → Architecture → Issue → PR → Report → Review → Merge → Next

### 1. Brain Storm
- Формулируется проблема/идея на языке пользователя и команды.
- Фиксируем ожидаемую ценность.

### 2. Формируем архитектуру
- Пишем как решение в целом встраивается в текущую систему.
- Проверяем не ломаем ли state-machine, память, напоминания.

### 3. Создаём GitHub Issue
- По очереди: Epic → Feature → Task/Bug.
- Для каждой задачи должны быть AC и Definition of Done.

### 4. Codex реализует
- Работаем по Issue, не выносимся из scope.
- По завершении задачи обязателен отчёт по шаблону `task-report.md`.

### 5. PR
- Минимальный, логичный, с тестами и описанием влияния.

### 6. Отчёт
- Структурированный отчёт по завершении задачи.
- Без общих фраз, только факты, риски, проверка.

### 7. Code Review
- Проверка качества, стиля, регрессий, полноты DoD.

### 8. Merge
- Сливаем только после DoD и review.

### 9. Следующая задача
- Обновляем roadmap и переносим приоритет.

---

## Иерархия задач

- **Epic** — большой блок изменений/направление.
- **Feature** — значимый набор возможностей внутри Epic.
- **Task** — узкая задачa с конкретным результатом.
- **Bug** — отклонение от ожидаемого поведения.

## Labels (рекомендуемые)

- epic
- feature
- task
- bug
- architecture
- telegram
- prompt
- ux
- performance
- database
- personality
- memory
- state-machine
- report
- project-process

## Milestones (рекомендуемые)

- **v0.2 — Живой характер**
- **v0.3 — State Machine**
- **v0.4 — Новая БД**
- **v0.5 — Напоминания**
- **v1.0 — MVP**

## Project Board (рекомендуемая канбан-доска)

- 💡 Ideas
- 📋 Backlog
- 🟡 Ready
- 🟣 In Progress
- 👀 Review
- ✅ Done

## Definition of Done (DoD) — обязательное закрытие задачи

Issue закрывается только при выполнении всех пунктов:

- [ ] Код
- [ ] Тесты
- [ ] Отчёт
- [ ] Code Review
- [ ] Обновлён roadmap

## Шаблоны

- `.github/ISSUE_TEMPLATE/epic.md`
- `.github/ISSUE_TEMPLATE/feature.md`
- `.github/ISSUE_TEMPLATE/task.md`
- `.github/ISSUE_TEMPLATE/bug.md`
- `.github/ISSUE_TEMPLATE/task-report.md`
- `.github/pull_request_template.md`
- `docs/process/issue-lifecycle.md`
- `docs/process/code-review-template.md`
