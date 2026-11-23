"""An isolated source-backed coach with an explicit paid-call boundary.

Mechanical checks constrain evidence and consent; they do not establish real
model usefulness. No generic context builder, Personal memory or tool execution
is available to this engine. Publication belongs to the final guarded outbox.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from app_v2.domain.coach import CoachJob, CoachPermissions, SourceProof
from app_v2.repositories.source_archive_repo import ArchiveSource
from app_v2.services.coach_ai import (
    CoachAIError, CoachAIConfigurationError, CoachBudgetConfigurationError,
    CoachBudgetExceeded, CoachOperationAlreadyAttempted,
)
from app_v2.services.coach_sources import CoachSourceSelection, CoachSourceSelector


_PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
_MODES = {'auto_report', 'direct', 'weekly_review', 'track', 'index'}
_KINDS = {'goal', 'plan', 'attempt', 'action', 'result', 'experiment'}
_GAPS = {
    'unclear_goal': 'В выбранных источниках нет достаточно ясной формулировки цели.',
    'outcome_not_reported': 'В выбранных источниках не подтверждён результат попытки.',
    'limited_selection': 'Это ограниченная выборка источников, а не полный разбор каждого сообщения.',
}
_EXPERIMENT_LABELS = {
    'offered': 'предложен, согласие ещё не получено',
    'accepted': 'принят участником', 'declined': 'отклонён участником',
    'tried_self_reported': 'попытка подтверждена самоотчётом',
    'not_tried_self_reported': 'участник сообщил, что не пробовал',
    'retired': 'закрыт',
}
_SENSITIVE = re.compile(
    r'не хочу жить|убить себя|суицид|самоубий|навредить себе|самоповрежден|'
    r'антидепрессант|психиатр|\bдиагноз\b|паническ\w* атак|'
    r'suicid|kill myself|self.harm', re.I)
_UNSAFE_OUTPUT = re.compile(
    r'https?://|t\.me/|@\w+|\bдиагноз\b|\bпсихопат\w*|\bнарцисс\w*|'
    r'ты (?:всегда|никогда)|you (?:always|never)|'
    r'дозировк|антидепрессант|возьми кредит|вложи деньги|обмани|уволься|'
    r'докажи что|не будь слаб|трус\w*|через боль', re.I)
_FIRST_TIME = re.compile(r'\bя\s+(?:впервые|в\s+первый\s+раз)\s+', re.I)
_AI_FAILURES = (CoachAIError, CoachAIConfigurationError, CoachBudgetConfigurationError,
                CoachBudgetExceeded, CoachOperationAlreadyAttempted)


@dataclass(frozen=True)
class CoachRequest:
    operation_id: str
    mode: str
    chat_id: int
    message_thread_id: int
    author_user_id: int
    current_text: str = ''
    source_id: str | None = None
    source_checksum: str | None = None
    consent_version: int | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    days: int = 7
    public_confirmed: bool = False
    contains_external_content: bool = False
    current_guard: Callable[[], bool] | None = field(default=None, repr=False, compare=False)
    originating_job: CoachJob | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CoachDecision:
    action: str
    text: str = ''
    dependencies: tuple[SourceProof, ...] = ()
    current_checksum: str | None = None
    consent_version: int | None = None
    reason: str = ''
    experiment_id: str | None = None
    structured: dict[str, Any] = field(default_factory=dict)
    messages: tuple[str, ...] = ()
    experiment_links: tuple[tuple[str, int], ...] = ()

    @property
    def source_proofs(self) -> tuple[SourceProof, ...]:
        return self.dependencies

    @property
    def texts(self) -> tuple[str, ...]:
        return self.messages


@dataclass(frozen=True)
class _Evidence:
    source_id: str
    checksum: str
    text: str
    source: ArchiveSource | None = None

    def data(self) -> dict:
        return {'source_id': self.source_id, 'checksum': self.checksum,
                'text': self.text, 'is_current_only': self.source is None,
                'occurred_at': self.source.occurred_at.isoformat() if self.source else None,
                'message_id': self.source.telegram_message_id if self.source else None}


def _obj(properties: dict) -> dict:
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


_REF = _obj({'source_id': {'type': 'string'}, 'checksum': {'type': 'string'}})
_REFS = {'type': 'array', 'items': _REF, 'minItems': 1, 'maxItems': 40}
_FACT = _obj({'kind': {'type': 'string', 'enum': sorted(_KINDS)},
              'quote': {'type': 'string', 'minLength': 1, 'maxLength': 600},
              'source_id': {'type': 'string'}, 'checksum': {'type': 'string'}})
_HYPOTHESIS = _obj({'text': {'type': 'string', 'maxLength': 400}, 'source_refs': _REFS})
_EXPERIMENT = _obj({
    'instruction': {'type': 'string', 'minLength': 1, 'maxLength': 500},
    'observation': {'type': 'string', 'minLength': 1, 'maxLength': 250},
    'minutes': {'type': 'integer', 'minimum': 1, 'maximum': 30},
    'reversible': {'type': 'boolean'}, 'voluntary': {'type': 'boolean'},
    'risk_level': {'type': 'string', 'enum': ['low']}, 'source_refs': _REFS,
    'message_index': {'type': 'integer', 'minimum': 0},
})
_BASE_PROPERTIES = {
    'facts': {'type': 'array', 'items': _FACT},
    'hypotheses': {'type': 'array', 'items': _HYPOTHESIS},
    'risk_flags': {'type': 'array', 'items': {'type': 'string'}},
    'first_time_claim': {'type': 'boolean'}, 'recurring_claim': {'type': 'boolean'},
}
ASSESSMENT_SCHEMA = _obj({**_BASE_PROPERTIES,
    'search_terms': {'type': 'array', 'items': {'type': 'string', 'minLength': 2, 'maxLength': 80}, 'maxItems': 6},
})
RESPONSE_SCHEMA = _obj({**_BASE_PROPERTIES,
    'action': {'type': 'string', 'enum': ['SILENCE', 'RESPOND']},
    'messages': {'type': 'array', 'items': _obj({
        'text': {'type': 'string', 'minLength': 1},
        'source_refs': {'type': 'array', 'items': _REF, 'maxItems': 40},
    })},
    'experiments': {'type': 'array', 'items': _EXPERIMENT},
    'gaps': {'type': 'array', 'items': {'type': 'string', 'enum': list(_GAPS)}, 'maxItems': 3},
})
TRACK_SCHEMA = RESPONSE_SCHEMA


class _Rejected(ValueError):
    pass


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _Rejected('invalid_output_text')
    if _UNSAFE_OUTPUT.search(value):
        raise _Rejected('unsafe_or_unsupported_output')
    return value.strip()


class CoachEngine:
    def __init__(self, *, archive, state, ai, selector=None,
                 max_response_bytes: int = 65536, conversation_max_bytes: int = 12000,
                 display_name: str | None = None) -> None:
        if (type(max_response_bytes) is not int or not 4096 <= max_response_bytes <= 1048576
                or type(conversation_max_bytes) is not int or not 0 <= conversation_max_bytes <= 65536):
            raise ValueError('Explicit bounded input/output byte budgets are required')
        self.archive, self.state, self.ai = archive, state, ai
        self.display_name = display_name
        self.selector = selector or CoachSourceSelector(archive)
        self.max_response_bytes, self.conversation_max_bytes = max_response_bytes, conversation_max_bytes

    @staticmethod
    def _allowed(request: CoachRequest, permissions: CoachPermissions) -> bool:
        if request.mode == 'auto_report':
            return permissions.automatic_allowed
        if request.mode == 'index':
            return permissions.retain_source and permissions.historical_inference and permissions.track_status == 'active'
        if request.mode in {'track', 'weekly_review'}:
            return (permissions.direct_request_group_reply and permissions.retain_source
                    and permissions.historical_inference
                    and (request.mode != 'track' or permissions.track_status == 'active'))
        return permissions.direct_request_group_reply

    def _check(self, request: CoachRequest, version: int, evidence: list[_Evidence]) -> bool:
        if not self.state.scope_enabled() or (request.current_guard is not None and not request.current_guard()):
            return False
        permissions = self.state.permissions(request.author_user_id)
        if permissions.version != version or not self._allowed(request, permissions):
            return False
        scope = dict(chat_id=request.chat_id, message_thread_id=request.message_thread_id,
                     author_user_id=request.author_user_id, actor_user_id=request.author_user_id)
        own_current = [e.source for e in evidence if e.source and e.source_id == request.source_id]
        history = [e.source for e in evidence if e.source and e.source_id != request.source_id]
        if own_current and not self.archive.reauthorize(own_current, **scope, purpose='owner_export'):
            return False
        if history and not self.archive.reauthorize(history, **scope, purpose='historical_inference'):
            return False
        return True

    def _call(self, request: CoachRequest, version: int, evidence: list[_Evidence],
              *, phase: str, prompt: str, schema: dict, coverage: dict,
              memory: list[dict] | None = None, conversation: list[dict] | None = None) -> dict:
        if not self._check(request, version, evidence):
            raise _Rejected('permission_or_source_changed')
        operation = 'coach:' + hashlib.sha256(request.operation_id.encode()).hexdigest() + ':' + request.mode + ':' + phase
        payload = {'mode': request.mode, 'phase': phase, 'sources': [e.data() for e in evidence],
                   'current_source_id': request.source_id or 'current:' + request.operation_id,
                   'coverage': coverage, 'requested_window_days': request.days,
                   'source_backed_memory': memory or [], 'conversation': conversation or [],
                   'output_byte_budget': self.max_response_bytes}
        system_prompt = (_PROMPTS / prompt).read_text()
        if self.display_name:
            system_prompt = 'Твоё имя: ' + json.dumps(self.display_name, ensure_ascii=False) + '.\n' + system_prompt
        result = self.ai.request_json(
            operation_id=operation, purpose=phase,
            messages=[{'role': 'system', 'content': system_prompt},
                      {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
            schema=schema,
        )
        if not self._check(request, version, evidence):
            raise _Rejected('permission_or_source_changed')
        try:
            self._validate(result.parsed, evidence, extraction=phase in {'assessment', 'index'})
        except (TypeError, KeyError, AttributeError) as exc:
            raise _Rejected('invalid_response_value_type') from exc
        return result.parsed

    @staticmethod
    def _refs(value: object, sources: dict[str, _Evidence], *, allow_empty: bool = False) -> list[str]:
        if not isinstance(value, list) or not (0 if allow_empty else 1) <= len(value) <= 40:
            raise _Rejected('source_refs_required')
        ids = []
        for ref in value:
            if not isinstance(ref, dict) or set(ref) != {'source_id', 'checksum'}:
                raise _Rejected('invalid_source_ref')
            source = sources.get(ref['source_id']) if isinstance(ref['source_id'], str) else None
            if source is None or source.checksum != ref['checksum']:
                raise _Rejected('unprovided_source_ref')
            ids.append(source.source_id)
        return ids

    def _validate(self, data: object, evidence: list[_Evidence], *, extraction: bool) -> None:
        schema = ASSESSMENT_SCHEMA if extraction else RESPONSE_SCHEMA
        if not isinstance(data, dict) or set(data) != set(schema['properties']):
            raise _Rejected('invalid_response_shape')
        if len(json.dumps(data, ensure_ascii=False).encode('utf-8')) > self.max_response_bytes:
            raise _Rejected('response_byte_budget_exceeded')
        sources = {e.source_id: e for e in evidence}
        if not isinstance(data['facts'], list):
            raise _Rejected('invalid_facts')
        for flag in ('first_time_claim', 'recurring_claim'):
            if type(data[flag]) is not bool:
                raise _Rejected('invalid_claim_flag')
        risk = data['risk_flags']
        if not isinstance(risk, list) or any(not isinstance(x, str) for x in risk):
            raise _Rejected('invalid_risk_flags')
        if risk:
            raise _Rejected('model_flagged_sensitive_content')
        for fact in data['facts']:
            if not isinstance(fact, dict) or set(fact) != {'kind', 'quote', 'source_id', 'checksum'}:
                raise _Rejected('invalid_fact')
            if (fact['kind'] not in _KINDS or not isinstance(fact['quote'], str)
                    or not fact['quote'].strip() or not 1 <= len(fact['quote']) <= 600):
                raise _Rejected('invalid_fact')
            self._refs([{'source_id': fact['source_id'], 'checksum': fact['checksum']}], sources)
            if fact['quote'] not in sources[fact['source_id']].text:
                raise _Rejected('fact_is_not_an_exact_source_quote')
        if not isinstance(data['hypotheses'], list):
            raise _Rejected('invalid_hypotheses')
        for hypothesis in data['hypotheses']:
            if not isinstance(hypothesis, dict) or set(hypothesis) != {'text', 'source_refs'}:
                raise _Rejected('invalid_hypothesis')
            _text(hypothesis['text'], 400)
            self._refs(hypothesis['source_refs'], sources)
        if data['first_time_claim'] and not any(_FIRST_TIME.search(f['quote']) for f in data['facts']):
            raise _Rejected('first_time_requires_explicit_self_report')
        if data['recurring_claim']:
            relevant = [sources[f['source_id']] for f in data['facts']]
            message_ids = {e.source.telegram_message_id for e in relevant if e.source}
            distinct_bodies = {' '.join(e.text.casefold().split()) for e in relevant if e.source}
            if len(message_ids) < 2 or len(distinct_bodies) < 2:
                raise _Rejected('recurrence_requires_independent_episodes')
        if extraction:
            terms = data['search_terms']
            if not isinstance(terms, list) or len(terms) > 6 or any(not isinstance(t, str) or not 2 <= len(t) <= 80 for t in terms):
                raise _Rejected('invalid_search_terms')
            return
        if data['action'] not in {'SILENCE', 'RESPOND'} or not isinstance(data['messages'], list):
            raise _Rejected('invalid_dialogue_action')
        if not isinstance(data['experiments'], list):
            raise _Rejected('invalid_experiments')
        if not isinstance(data['gaps'], list) or len(data['gaps']) > 3 or any(g not in _GAPS for g in data['gaps']):
            raise _Rejected('invalid_gap')
        if data['action'] == 'SILENCE':
            if data['messages'] or data['experiments']:
                raise _Rejected('silence_has_no_publication')
        elif not data['messages']:
            raise _Rejected('response_text_required')
        for message in data['messages']:
            if not isinstance(message, dict) or set(message) != {'text', 'source_refs'}:
                raise _Rejected('invalid_dialogue_message')
            _text(message['text'], self.max_response_bytes)
            self._refs(message['source_refs'], sources, allow_empty=True)
        for experiment in data['experiments']:
            if not isinstance(experiment, dict) or set(experiment) != set(_EXPERIMENT['properties']):
                raise _Rejected('invalid_experiment')
            _text(experiment['instruction'], 500)
            _text(experiment['observation'], 250)
            if type(experiment['minutes']) is not int or not 1 <= experiment['minutes'] <= 30:
                raise _Rejected('unbounded_experiment')
            if experiment['reversible'] is not True or experiment['voluntary'] is not True or experiment['risk_level'] != 'low':
                raise _Rejected('unsafe_experiment')
            refs = self._refs(experiment['source_refs'], sources)
            part = experiment['message_index']
            if type(part) is not int or not 0 <= part < len(data['messages']):
                raise _Rejected('experiment_message_required')
            message = data['messages'][part]
            if (experiment['instruction'] not in message['text']
                    or experiment['observation'] not in message['text']
                    or not set(refs).issubset({ref['source_id'] for ref in message['source_refs']})):
                raise _Rejected('experiment_must_be_in_the_linked_message')

    @staticmethod
    def _dependencies(evidence: list[_Evidence]) -> tuple[SourceProof, ...]:
        return tuple(SourceProof(e.source_id, e.checksum, e.source.access_version)
                     for e in evidence if e.source is not None)

    def _persist_facts(self, request: CoachRequest, permissions: CoachPermissions,
                       evidence: list[_Evidence], data: dict) -> int:
        if request.originating_job is None or not (permissions.retain_source and permissions.historical_inference):
            return 0
        if any(e.source is None for e in evidence):
            # A transient request may influence categorization. Do not persist
            # a derived episode whose full input provenance cannot be retained.
            return 0
        sources = {e.source_id: e for e in evidence}
        stored = 0
        dependencies = self._dependencies(evidence)
        for fact in data['facts']:
            source = sources[fact['source_id']].source
            if source is None:
                continue  # Current-only requests never become durable episodes.
            dedupe = hashlib.sha256((fact['kind'] + '\0' + ' '.join(fact['quote'].casefold().split())).encode()).hexdigest()
            saved = self.state.save_episode(
                author_user_id=request.author_user_id, episode_type=fact['kind'],
                summary=fact['quote'], source_proofs=dependencies,
                dedupe_key=dedupe, originating_job=request.originating_job,
            )
            stored += bool(saved)
        return stored

    def _memory_context(self, request: CoachRequest, evidence: list[_Evidence],
                        *, since: datetime, until: datetime) -> list[dict]:
        """Resolve bounded cards back to original sources before any use.

        Cards supplement the full-window SQL selection. They are never accepted
        as independent evidence or as proof that a proposed experiment happened.
        """
        cards = self.state.list_episodes(author_user_id=request.author_user_id,
                                         limit=80, since=since, until=until)
        experiments = self.state.list_experiments(author_user_id=request.author_user_id, limit=20)
        by_kind: dict[str, list] = {kind: [] for kind in _KINDS}
        for card in cards:
            if card['episode_type'] == 'experiment' and card.get('status') == 'prepared':
                continue
            if card['episode_type'] in by_kind:
                by_kind[card['episode_type']].append(card)
        selected = [card for kind in sorted(by_kind) for card in by_kind[kind][:2]]
        selected.extend(experiments[:8])
        known = {e.source_id for e in evidence}
        seen_cards: set[str] = set()
        result = []
        for card in selected:
            if card['id'] in seen_cards:
                continue
            seen_cards.add(card['id'])
            additions, valid = [], True
            for proof in card['source_proofs']:
                source = self.selector.current(chat_id=request.chat_id, message_thread_id=request.message_thread_id,
                    author_user_id=request.author_user_id, source_id=proof.source_id)
                if (source is None or source.checksum != proof.checksum or source.access_version != proof.access_version
                        or source.contains_external_content or not source.available_text
                        or _SENSITIVE.search(source.available_text)):
                    valid = False
                    break
                if source.source_id not in known:
                    additions.append(_Evidence(source.source_id, source.checksum, source.available_text[:1200], source))
            if not valid or len(known | {e.source_id for e in additions}) > 40:
                continue
            for item in additions:
                if item.source_id not in known:
                    evidence.append(item)
                    known.add(item.source_id)
            result.append({'episode_type': card['episode_type'], 'summary': card['summary'],
                           'status': card.get('status'),
                           'source_refs': [{'source_id': p.source_id, 'checksum': p.checksum}
                                           for p in card['source_proofs']]})
        return result

    def _conversation_context(self, request: CoachRequest, evidence: list[_Evidence]) -> list[dict]:
        """Previous delivered replies explain follow-ups but are not user facts."""
        if not self.conversation_max_bytes:
            return []
        records = self.state.conversation_context(request.author_user_id,
            max_bytes=self.conversation_max_bytes, before=request.now + timedelta(seconds=1))
        known = {e.source_id for e in evidence}
        result, used = [], 0
        # The repository returns a recent set in chronological order. Apply
        # the stricter JSON/proof byte budget to the newest turns first, then
        # restore chronological presentation for the model.
        for record in reversed(records):
            if (not isinstance(record, dict) or record.get('role') not in {'user', 'assistant'}
                    or not isinstance(record.get('text'), str) or not record['text'].strip()
                    or not record.get('source_proofs') or _SENSITIVE.search(record['text'])):
                continue
            additions, resolved, valid = [], [], True
            for proof in record['source_proofs']:
                if not isinstance(proof, SourceProof):
                    valid = False
                    break
                source = self.selector.current(chat_id=request.chat_id, message_thread_id=request.message_thread_id,
                    author_user_id=request.author_user_id, source_id=proof.source_id)
                if (source is None or source.checksum != proof.checksum or source.access_version != proof.access_version
                        or source.contains_external_content or not source.available_text
                        or (source.chat_id, source.message_thread_id, source.author_user_id) !=
                            (request.chat_id, request.message_thread_id, request.author_user_id)
                        or _SENSITIVE.search(source.available_text)):
                    valid = False
                    break
                resolved.append(source)
                if source.source_id not in known:
                    additions.append(_Evidence(source.source_id, source.checksum, source.available_text[:1200], source))
            if not valid or len(known | {e.source_id for e in additions}) > 40:
                continue
            user_source = None
            if record['role'] == 'user':
                # The user turn must be the exact archived message. A delivered
                # assistant turn can refer to several sources, but its wording
                # must never be promoted into evidence of what the user said.
                if (len(resolved) != 1 or record.get('telegram_message_id') != resolved[0].telegram_message_id
                        or record['text'] != resolved[0].available_text):
                    continue
                user_source = resolved[0]
            at = user_source.occurred_at if user_source else record.get('occurred_at')
            item = {'role': record['role'], 'text': record['text'],
                    'telegram_message_id': record.get('telegram_message_id'),
                    'occurred_at': at.isoformat() if isinstance(at, datetime) else None,
                    'source_refs': [{'source_id': p.source_id, 'checksum': p.checksum}
                                    for p in record['source_proofs']]}
            size = len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
            if used + size > self.conversation_max_bytes:
                continue
            if user_source is not None:
                # This full text already fits the explicit conversation byte
                # budget. Give quote validation the same text the model sees,
                # including when an earlier turn introduced a shorter excerpt.
                expanded = _Evidence(user_source.source_id, user_source.checksum,
                                     user_source.available_text, user_source)
                if user_source.source_id in known:
                    evidence[:] = [expanded if e.source_id == user_source.source_id else e for e in evidence]
                else:
                    additions = [expanded if e.source_id == user_source.source_id else e for e in additions]
            for addition in additions:
                if addition.source_id not in known:
                    evidence.append(addition)
                    known.add(addition.source_id)
            used += size
            result.append(item)
        return list(reversed(result))

    @staticmethod
    def _telegram_parts(text: str) -> list[str]:
        """Split only at Telegram's UTF-16 size boundary, without a part quota."""
        parts, current, units = [], [], 0
        for char in text:
            width = 2 if ord(char) > 0xffff else 1
            if units + width > 4096:
                parts.append(''.join(current))
                current, units = [], 0
            current.append(char)
            units += width
        if current:
            parts.append(''.join(current))
        return parts

    def _render(self, data: dict, evidence: list[_Evidence], coverage: dict, *, track: bool,
                memory: list[dict] | None = None) -> tuple[tuple[str, ...], dict[int, int]]:
        sources = {e.source_id: e for e in evidence}
        rendered, indices = [], {}
        for index, message in enumerate(data['messages']):
            text = message['text'].strip()
            if track and message['source_refs']:
                cited = []
                for ref in message['source_refs']:
                    source = sources[ref['source_id']].source
                    if source is not None:
                        cited.append(f'сообщение №{source.telegram_message_id}, {source.occurred_at:%d.%m.%Y}')
                if cited:
                    text += '\n\nИсточники: ' + '; '.join(dict.fromkeys(cited)) + '.'
            rendered.extend(self._telegram_parts(text))
            indices[index] = len(rendered) - 1
        if track:
            notes = [_GAPS[gap] for gap in data['gaps']]
            experiment_cards = [c for c in (memory or []) if c.get('status') in _EXPERIMENT_LABELS]
            if experiment_cards:
                notes.append('Статусы выбранных экспериментов на момент разбора:')
                for card in experiment_cards:
                    notes.append('• ' + card['summary'][:160] + ' — ' + _EXPERIMENT_LABELS[card['status']])
            coverage_line = (f'Основание: {coverage["selected_sources"]} выбранных источников из '
                             f'{coverage["eligible_text_sources_in_window"]} доступных за указанный период. '
                             'Неизвестные пропуски доставки и содержимое медиа не восстановлены.')
            if coverage.get('supporting_sources_outside_window', 0):
                coverage_line += f' Дополнительных источников вне периода: {coverage["supporting_sources_outside_window"]}.'
            notes.append(coverage_line)
            rendered.extend(self._telegram_parts('\n\n'.join(notes)))
        if sum(len(part.encode('utf-8')) for part in rendered) > self.max_response_bytes:
            raise _Rejected('response_byte_budget_exceeded')
        return tuple(rendered), indices

    def process(self, request: CoachRequest) -> CoachDecision:
        def silence(reason: str, *, version: int | None = None, structured: dict | None = None) -> CoachDecision:
            return CoachDecision('SILENCE', current_checksum=request.source_checksum,
                                 consent_version=version, reason=reason, structured=structured or {})
        if (request.mode not in _MODES or not isinstance(request.operation_id, str)
                or not request.operation_id or request.now.tzinfo is None
                or not isinstance(request.current_text, str)):
            raise ValueError('A valid mode, operation identity and aware timestamp are required')
        if request.current_text and (not isinstance(request.source_checksum, str)
                                    or re.fullmatch(r'[0-9a-f]{64}', request.source_checksum) is None):
            raise ValueError('The authenticated current snapshot checksum is required')
        if (type(request.days) is not int or not 1 <= request.days <= 3660
                or type(request.chat_id) is not int or request.chat_id >= 0
                or type(request.message_thread_id) is not int or request.message_thread_id <= 0
                or type(request.author_user_id) is not int or request.author_user_id <= 0):
            raise ValueError('Explicit group/topic/author and a bounded date window are required')
        if getattr(self.state, 'chat_id', request.chat_id) != request.chat_id or getattr(self.state, 'message_thread_id', request.message_thread_id) != request.message_thread_id:
            raise ValueError('Request does not belong to this configured coach scope')
        if request.originating_job is not None:
            job = request.originating_job
            if not isinstance(job, CoachJob) or any(
                    getattr(job, name) != getattr(request, name) for name in (
                        'mode', 'chat_id', 'message_thread_id', 'author_user_id', 'current_text',
                        'source_id', 'source_checksum', 'consent_version')):
                raise ValueError('The originating job must match the authenticated request')
        if request.current_guard is not None:
            if not callable(request.current_guard):
                raise ValueError('The runtime current guard must be callable')
            if not request.current_guard():
                return silence('stale_job_current')
        permissions = self.state.permissions(request.author_user_id)
        if not self.state.scope_enabled() or not self._allowed(request, permissions):
            return silence('permission_denied', version=permissions.version)
        if request.consent_version is not None and request.consent_version != permissions.version:
            return silence('stale_job_consent', version=permissions.version)
        if request.contains_external_content or _SENSITIVE.search(request.current_text):
            return silence('sensitive_or_external_current_source', version=permissions.version)
        current = None
        if request.source_id:
            current = self.selector.current(chat_id=request.chat_id, message_thread_id=request.message_thread_id,
                author_user_id=request.author_user_id, source_id=request.source_id)
            if current is None or current.checksum != request.source_checksum or current.contains_external_content:
                return silence('current_source_unavailable', version=permissions.version)
            if current.available_text != request.current_text:
                return silence('current_source_body_mismatch', version=permissions.version)
        elif request.mode in {'index', 'auto_report'}:
            return silence('automatic_processing_requires_durable_source', version=permissions.version)
        evidence: list[_Evidence] = []
        if request.current_text and request.mode not in {'track', 'weekly_review'}:
            evidence.append(_Evidence(request.source_id or 'current:' + request.operation_id,
                request.source_checksum or hashlib.sha256(request.current_text.encode()).hexdigest(),
                request.current_text[:4000], current))
        selection = CoachSourceSelection()
        coverage = selection.disclosure()
        memory: list[dict] = []
        conversation: list[dict] = []
        try:
            if permissions.historical_inference and permissions.retain_source:
                conversation = self._conversation_context(request, evidence)
            if request.mode not in {'track', 'weekly_review'}:
                if not evidence:
                    return silence('no_current_text', version=permissions.version)
                assessed = self._call(request, permissions.version, evidence,
                    phase='index' if request.mode == 'index' else 'assessment',
                    prompt='coach_assessment.md', schema=ASSESSMENT_SCHEMA, coverage=coverage,
                    conversation=conversation)
                indexed = self._persist_facts(request, permissions, evidence, assessed)
                if request.mode == 'index':
                    return silence('indexed_without_publication', version=permissions.version,
                                   structured={'facts_indexed': indexed})
                terms = assessed['search_terms']
            else:
                terms = []
            if permissions.historical_inference and permissions.retain_source:
                since = (request.now - timedelta(days=request.days)
                         if request.mode in {'track', 'weekly_review'} else datetime(1970, 1, 1, tzinfo=timezone.utc))
                selection = self.selector.select(
                    chat_id=request.chat_id, message_thread_id=request.message_thread_id,
                    author_user_id=request.author_user_id, since=since,
                    until=request.now + timedelta(seconds=1), terms=terms, limit=24,
                )
                known = {e.source_id for e in evidence}
                for source in selection.sources:
                    if (len(known) < 40 and source.source_id not in known and source.available_text
                            and not source.contains_external_content
                            and (source.chat_id, source.message_thread_id, source.author_user_id) ==
                                (request.chat_id, request.message_thread_id, request.author_user_id)
                            and not _SENSITIVE.search(source.available_text)):
                        evidence.append(_Evidence(source.source_id, source.checksum, source.available_text[:1200], source))
                        known.add(source.source_id)
                coverage = selection.disclosure()
                memory = self._memory_context(request, evidence, since=since, until=request.now + timedelta(seconds=1))
                coverage['selected_sources'] = len([e for e in evidence if e.source and since <= e.source.occurred_at < request.now + timedelta(seconds=1)])
                coverage['provided_source_count'] = len([e for e in evidence if e.source])
                coverage['supporting_sources_outside_window'] = coverage['provided_source_count'] - coverage['selected_sources']
                coverage['omitted_eligible_sources'] = max(0, selection.eligible_sources - coverage['selected_sources'])
            if not evidence:
                text = ('За выбранный период нет разрешённых текстовых источников для такого разбора. '
                        'Это не означает, что событий не было.')
                return CoachDecision('RESPOND', text, consent_version=permissions.version,
                    current_checksum=request.source_checksum, reason='no_authorized_evidence',
                    structured={'coverage': coverage}, messages=(text,))
            track = request.mode in {'track', 'weekly_review'}
            generated = self._call(request, permissions.version, evidence,
                phase='track' if request.mode == 'track' else 'review' if track else 'generation',
                prompt='coach_track.md' if track else 'coach_reply.md',
                schema=RESPONSE_SCHEMA, coverage=coverage, memory=memory, conversation=conversation)
            if not self._check(request, permissions.version, evidence):
                raise _Rejected('permission_or_source_changed')
            self._persist_facts(request, permissions, evidence, generated)
            if generated['action'] == 'SILENCE':
                return silence('generated_silence', version=permissions.version)
            dependencies = self._dependencies(evidence)
            for experiment in generated['experiments']:
                if any(c.get('status') in _EXPERIMENT_LABELS and
                       ' '.join(c['summary'].casefold().split()) == ' '.join(experiment['instruction'].casefold().split())
                       for c in memory):
                    raise _Rejected('known_experiment_must_not_be_reoffered')
            messages, indices = self._render(generated, evidence, coverage, track=track, memory=memory)
            links_by_id: dict[str, int] = {}
            if (request.originating_job is not None and dependencies and all(e.source is not None for e in evidence)
                    and permissions.retain_source and permissions.historical_inference):
                for experiment in generated['experiments']:
                    identifier = self.state.save_experiment(
                        author_user_id=request.author_user_id, proposal=experiment['instruction'],
                        source_proofs=dependencies, status='prepared', originating_job=request.originating_job,
                    )
                    if identifier:
                        # Identical source-backed proposals can share a durable
                        # ID. Offer it only after all its answer parts arrive.
                        links_by_id[identifier] = max(links_by_id.get(identifier, -1),
                                                      indices[experiment['message_index']])
            links = tuple(links_by_id.items())
            return CoachDecision('RESPOND', '\n\n'.join(messages),
                dependencies, request.source_checksum, permissions.version, 'source_backed_decision',
                links[0][0] if links else None,
                {'coverage': coverage, 'result': generated, 'conversation_turns': len(conversation),
                 'all_provided_source_ids': [e.source_id for e in evidence]},
                messages=messages, experiment_links=links)
        except _AI_FAILURES as exc:
            return silence(type(exc).__name__, version=permissions.version)
        except _Rejected as exc:
            return silence(str(exc), version=permissions.version)
