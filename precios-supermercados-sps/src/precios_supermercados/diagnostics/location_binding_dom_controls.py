"""Resolución DOM fail-closed para controles de ubicación.

Este módulo no abre red, no conoce targets live ni concede autoridad. Sólo recibe
una página Playwright ya existente y resuelve, de forma exacta y única, el selector
visible de ubicación y los controles interactivos de ciudad. Se mantiene separado
de la autorización live para poder probar el contrato completamente offline.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# La evidencia HTML de La Colonia confirma botones de ciudad dentro de
# ``.cont-btn-ciudad`` con estado ``selected`` / ``noselected``. Esa estructura se
# resuelve antes que heurísticas ARIA. La página real además puede montar dos copias
# DOM perfectamente superpuestas del mismo modal; sólo se colapsan cuando ocupan la
# misma superficie física y el hit-test demuestra una única copia superior.
CITY_CONTROL_ROLES: tuple[str, ...] = ("radio", "option", "menuitem", "button")
CITY_CONTROL_READY_TIMEOUT_MS = 5_000
CITY_CONTROL_READY_POLL_MS = 100
LOCATION_SELECTOR_OPEN_TIMEOUT_MS = 5_000
LOCATION_SELECTOR_OPEN_POLL_MS = 500
LOCATION_SELECTOR_EFFECTIVE_RETRY_DELAY_MS = 3_000
CITY_MODAL_SCOPE_MAX_ANCESTORS = 8
LOCATION_SELECTOR_CLASS = "btn-modal-selector"
LOCATION_SELECTOR_CSS = f"button.{LOCATION_SELECTOR_CLASS}"
CITY_BUTTON_CONTAINER_CLASS = "cont-btn-ciudad"
CITY_BUTTON_SELECTED_CLASS = "btn-ciudad-selected"
CITY_BUTTON_UNSELECTED_CLASS = "btn-ciudad-noselected"
CITY_BUTTON_CONTAINER_CSS = f".{CITY_BUTTON_CONTAINER_CLASS}"
CITY_BUTTON_CSS = (
    f"button.{CITY_BUTTON_SELECTED_CLASS}, "
    f"button.{CITY_BUTTON_UNSELECTED_CLASS}"
)
CITY_STATE_SELECTED = "selected"
CITY_STATE_UNSELECTED = "unselected"
CITY_MODAL_PROMPT_PATTERN = re.compile(
    r"^\s*¿?\s*desde\s+qu[eé]\s+ciudad\s+nos\s+visita\??\s*$",
    re.I,
)
_LOCATION_SELECTOR_ACCESSIBLE_PATTERN = re.compile(
    r"selecciona\s+tu\s+tienda|selecciona\s+una\s+tienda|ubicaci[oó]n",
    re.I,
)
_IGNORED_CITY_LABELS = frozenset(
    {"selecciona tu ciudad", "selecciona una ciudad", "ciudad"}
)
_IGNORED_VISIBLE_LOCATION_LABELS = frozenset(
    {
        "selecciona tu tienda",
        "selecciona una tienda",
        "selecciona tu ciudad",
        "selecciona una ciudad",
        "ubicacion",
        "ubicación",
        "ciudad",
        "tienda",
    }
)
_VIEWPORT_INTERSECTION_JS = """
(element) => {
  const rect = element.getBoundingClientRect();
  const width = window.innerWidth || document.documentElement.clientWidth || 0;
  const height = window.innerHeight || document.documentElement.clientHeight || 0;
  return (
    rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0 &&
    rect.left < width && rect.top < height
  );
}
"""
_DOM_PATH_JS = """
(element) => {
  const path = [];
  let current = element;
  while (current && current.parentElement) {
    path.unshift(Array.prototype.indexOf.call(current.parentElement.children, current));
    current = current.parentElement;
  }
  return path;
}
"""
_VISUAL_RECT_JS = """
(element) => {
  const rect = element.getBoundingClientRect();
  return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
}
"""
_TOPMOST_AT_CENTER_JS = """
(element) => {
  const rect = element.getBoundingClientRect();
  if (!(rect.width > 0 && rect.height > 0)) return false;
  const x = Math.max(0, Math.min((window.innerWidth || 1) - 1, rect.left + rect.width / 2));
  const y = Math.max(0, Math.min((window.innerHeight || 1) - 1, rect.top + rect.height / 2));
  const hit = document.elementFromPoint(x, y);
  return Boolean(hit && (hit === element || element.contains(hit)));
}
"""
_LOCATION_SELECTOR_HANDLER_JS = """
(element) => {
  if (typeof element.onclick === 'function') return true;
  for (const key of Object.keys(element)) {
    if (
      key.startsWith('__reactProps$') ||
      key.startsWith('__reactEventHandlers$')
    ) {
      const props = element[key];
      if (props && typeof props.onClick === 'function') return true;
    }
  }
  return false;
}
"""
_VISUAL_RECT_TOLERANCE_PX = 1.5


class LocationControlResolutionError(RuntimeError):
    """Fallo determinista con diagnóstico sanitizado opcional."""

    def __init__(
        self,
        reason: str,
        *,
        diagnostic: Mapping[str, str | int] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = dict(diagnostic or {})


@dataclass(frozen=True, slots=True)
class ResolvedLocationSelector:
    """Botón único que abre el selector y ubicación visible, si la expone."""

    locator: Any
    source: str
    visible_location: str | None


@dataclass(frozen=True, slots=True)
class ResolvedCityControl:
    """Control exacto y único junto con etiquetas públicas observables."""

    locator: Any
    role: str
    available_cities: tuple[str, ...]
    state: str | None = None


def _clean_label(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


def _label(locator: Any) -> str | None:
    try:
        aria = _clean_label(locator.get_attribute("aria-label"))
    except Exception:
        aria = None
    if aria:
        return aria
    try:
        return _clean_label(locator.inner_text())
    except Exception:
        return None


def _has_css_class(locator: Any, class_name: str) -> bool:
    try:
        raw = locator.get_attribute("class") or ""
    except Exception:
        return False
    return class_name in str(raw).split()


def _structural_city_state(locator: Any) -> str:
    selected = _has_css_class(locator, CITY_BUTTON_SELECTED_CLASS)
    unselected = _has_css_class(locator, CITY_BUTTON_UNSELECTED_CLASS)
    if selected == unselected:
        raise LocationControlResolutionError("target_city_state_invalid")
    return CITY_STATE_SELECTED if selected else CITY_STATE_UNSELECTED


def _is_presented_in_viewport(locator: Any) -> bool:
    try:
        if not locator.is_visible():
            return False
    except Exception:
        return False
    evaluator = getattr(locator, "evaluate", None)
    if evaluator is None:
        return True
    try:
        return bool(evaluator(_VIEWPORT_INTERSECTION_JS))
    except Exception:
        return False


def _dom_path(locator: Any) -> tuple[int, ...] | None:
    evaluator = getattr(locator, "evaluate", None)
    if evaluator is None:
        return None
    try:
        raw = evaluator(_DOM_PATH_JS)
    except Exception:
        return None
    if not isinstance(raw, list) or any(
        not isinstance(index, int) or index < 0 for index in raw
    ):
        return None
    return tuple(raw)


def _visual_rect(locator: Any) -> tuple[float, float, float, float] | None:
    """Obtiene geometría efímera del control; nunca se persiste en evidencia."""

    evaluator = getattr(locator, "evaluate", None)
    if evaluator is None:
        return None
    try:
        raw = evaluator(_VISUAL_RECT_JS)
    except Exception:
        return None
    if not isinstance(raw, Mapping):
        return None
    values: list[float] = []
    for key in ("x", "y", "width", "height"):
        value = raw.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        values.append(float(value))
    if values[2] <= 0 or values[3] <= 0:
        return None
    return values[0], values[1], values[2], values[3]


def _same_visual_rect(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return all(
        abs(a - b) <= _VISUAL_RECT_TOLERANCE_PX for a, b in zip(left, right)
    )


def _is_topmost_at_center(locator: Any) -> bool:
    evaluator = getattr(locator, "evaluate", None)
    if evaluator is None:
        return False
    try:
        return bool(evaluator(_TOPMOST_AT_CENTER_JS))
    except Exception:
        return False


def _collapse_nested_presented_items(items: list[Any]) -> list[Any]:
    """Colapsa sólo coincidencias ancestro/descendiente del mismo gesto visual."""

    if len(items) < 2:
        return items
    paths = [_dom_path(item) for item in items]
    if any(path is None for path in paths):
        return items
    typed_paths = [path for path in paths if path is not None]
    kept: list[Any] = []
    seen: set[tuple[int, ...]] = set()
    for index, path in enumerate(typed_paths):
        if path in seen:
            continue
        seen.add(path)
        is_ancestor_duplicate = any(
            len(path) < len(other) and other[: len(path)] == path
            for other in typed_paths
        )
        if not is_ancestor_duplicate:
            kept.append(items[index])
    return kept


def _collapse_overlapping_presented_items(items: list[Any]) -> list[Any]:
    """Colapsa copias DOM físicamente idénticas sólo con hit-test inequívoco.

    La página live observada monta dos árboles de modal superpuestos píxel a píxel.
    Dos controles en posiciones distintas continúan siendo ambiguos. Para un grupo
    con la misma geometría sólo se conserva un control si ``elementFromPoint``
    demuestra exactamente una copia superior/interactuable; ante cualquier duda se
    mantienen todas y la resolución posterior falla cerrada.
    """

    if len(items) < 2:
        return items
    rects = [_visual_rect(item) for item in items]
    groups: list[list[int]] = []
    assigned: set[int] = set()
    for index, rect in enumerate(rects):
        if index in assigned:
            continue
        group = [index]
        assigned.add(index)
        if rect is not None:
            for other_index in range(index + 1, len(items)):
                other = rects[other_index]
                if (
                    other_index not in assigned
                    and other is not None
                    and _same_visual_rect(rect, other)
                ):
                    group.append(other_index)
                    assigned.add(other_index)
        groups.append(group)

    kept: list[Any] = []
    for group in groups:
        if len(group) == 1 or rects[group[0]] is None:
            kept.extend(items[index] for index in group)
            continue
        topmost = [index for index in group if _is_topmost_at_center(items[index])]
        if len(topmost) == 1:
            kept.append(items[topmost[0]])
        else:
            kept.extend(items[index] for index in group)
    return kept


def _visible_items(collection: Any) -> list[Any]:
    visible: list[Any] = []
    for index in range(collection.count()):
        candidate = collection.nth(index)
        try:
            if candidate.is_visible():
                visible.append(candidate)
        except Exception:
            continue
    return visible


def _presented_items(collection: Any) -> list[Any]:
    presented: list[Any] = []
    for index in range(collection.count()):
        candidate = collection.nth(index)
        if _is_presented_in_viewport(candidate):
            presented.append(candidate)
    return presented


def _structural_city_button(page: Any, normalized: str) -> ResolvedCityControl | None:
    """Resuelve la estructura exacta confirmada para el selector de La Colonia."""

    try:
        containers = _collapse_overlapping_presented_items(
            _collapse_nested_presented_items(
                _presented_items(page.locator(CITY_BUTTON_CONTAINER_CSS))
            )
        )
    except Exception:
        return None
    if not containers:
        return None

    target_matches: list[tuple[Any, tuple[str, ...], str]] = []
    for container in containers:
        try:
            buttons = _presented_items(container.locator(CITY_BUTTON_CSS))
        except Exception:
            continue
        labels: list[str] = []
        exact_matches: list[Any] = []
        for button in buttons:
            label = _label(button)
            if not label or label.casefold() in _IGNORED_CITY_LABELS:
                continue
            labels.append(label)
            if label.casefold() == normalized.casefold():
                exact_matches.append(button)
        available = tuple(sorted(set(labels), key=str.casefold))
        for button in exact_matches:
            target_matches.append(
                (button, available or (normalized,), _structural_city_state(button))
            )

    candidate_count = len(target_matches)
    if candidate_count > 1:
        collapsed_locators = _collapse_overlapping_presented_items(
            [match[0] for match in target_matches]
        )
        collapsed_ids = {id(locator) for locator in collapsed_locators}
        target_matches = [
            match for match in target_matches if id(match[0]) in collapsed_ids
        ]
    if len(target_matches) > 1:
        raise LocationControlResolutionError(
            "target_city_not_unique",
            diagnostic={
                "stage": "role",
                "role": "button",
                "candidate_count": candidate_count,
                "effective_count": len(target_matches),
            },
        )
    if len(target_matches) == 1:
        locator, labels, state = target_matches[0]
        return ResolvedCityControl(
            locator=locator,
            role="button",
            available_cities=labels,
            state=state,
        )
    return None


def _visible_location(label: str | None) -> str | None:
    normalized = _clean_label(label)
    if normalized is None or normalized.casefold() in _IGNORED_VISIBLE_LOCATION_LABELS:
        return None
    return normalized


def resolve_location_selector(page: Any) -> ResolvedLocationSelector:
    try:
        structural = _collapse_overlapping_presented_items(
            _visible_items(page.locator(LOCATION_SELECTOR_CSS))
        )
    except Exception:
        structural = []
    if len(structural) > 1:
        raise LocationControlResolutionError("location_selector_not_unique")
    if len(structural) == 1:
        label = _label(structural[0])
        if label is None:
            raise LocationControlResolutionError("location_selector_label_missing")
        return ResolvedLocationSelector(
            locator=structural[0],
            source="btn-modal-selector",
            visible_location=_visible_location(label),
        )

    try:
        accessible = _collapse_overlapping_presented_items(
            _visible_items(
                page.get_by_role("button", name=_LOCATION_SELECTOR_ACCESSIBLE_PATTERN)
            )
        )
    except Exception:
        accessible = []
    if not accessible:
        raise LocationControlResolutionError("location_selector_not_found")
    if len(accessible) != 1:
        raise LocationControlResolutionError("location_selector_not_unique")
    return ResolvedLocationSelector(
        locator=accessible[0],
        source="accessible-fallback",
        visible_location=_visible_location(_label(accessible[0])),
    )


def _location_selector_modal_presented(page: Any) -> bool:
    """Confirma que el click del opener produjo una superficie real del modal."""

    try:
        if _presented_items(page.locator(CITY_BUTTON_CONTAINER_CSS)):
            return True
    except Exception:
        pass
    try:
        if _presented_items(page.get_by_text(CITY_MODAL_PROMPT_PATTERN)):
            return True
    except Exception:
        pass
    return False


def _location_selector_has_attached_handler(locator: Any) -> bool:
    """Detecta handlers observables sin depender del framework como identidad.

    ``onclick`` cubre handlers DOM directos. React conserva las props del nodo bajo
    claves privadas con prefijos estables por familia; esta señal es sólo
    diagnóstica: un handler presente no demuestra que el gesto haya producido un
    modal ni que el estado de la aplicación esté listo para aceptar el click.
    """

    evaluator = getattr(locator, "evaluate", None)
    if evaluator is None:
        return False
    try:
        return bool(evaluator(_LOCATION_SELECTOR_HANDLER_JS))
    except Exception:
        return False


def _wait_for_next_location_selector_probe(page: Any) -> bool:
    waiter = getattr(page, "wait_for_timeout", None)
    if waiter is None:
        return False
    try:
        waiter(LOCATION_SELECTOR_OPEN_POLL_MS)
    except Exception:
        return False
    return True


def open_location_selector(page: Any) -> ResolvedLocationSelector:
    """Abre el modal y exige evidencia del efecto, no sólo un handler conectado.

    La página puede renderizar ``btn-modal-selector`` y hasta exponer un handler
    React antes de que el estado hidratado acepte el gesto. Por eso el primer click
    sólo se considera efectivo cuando aparece una superficie real del modal. Si no
    ocurre, se espera el mismo intervalo de asentamiento que demostró la radiografía
    live y se emite un único segundo click. No se repite indefinidamente ni se usa la
    presencia del handler como prueba de éxito.
    """

    resolved = resolve_location_selector(page)
    resolved.locator.click()
    if _location_selector_modal_presented(page):
        return resolved

    wait_count = LOCATION_SELECTOR_OPEN_TIMEOUT_MS // LOCATION_SELECTOR_OPEN_POLL_MS
    elapsed_ms = 0
    retried = False
    for _ in range(wait_count):
        if not _wait_for_next_location_selector_probe(page):
            return resolved
        elapsed_ms += LOCATION_SELECTOR_OPEN_POLL_MS
        if _location_selector_modal_presented(page):
            return resolved
        if not retried and elapsed_ms >= LOCATION_SELECTOR_EFFECTIVE_RETRY_DELAY_MS:
            resolved = resolve_location_selector(page)
            resolved.locator.click()
            retried = True
            if _location_selector_modal_presented(page):
                return resolved
    return resolved


def verify_structural_city_selection(page: Any, city_name: str) -> str | None:
    normalized = _clean_label(city_name)
    if normalized is None:
        raise LocationControlResolutionError("target_city_name_invalid")
    try:
        header = resolve_location_selector(page)
    except LocationControlResolutionError:
        header = None
    visible_location = header.visible_location if header is not None else None
    if visible_location is not None and visible_location.casefold() != normalized.casefold():
        raise LocationControlResolutionError("visible_location_mismatch")
    structural = _structural_city_button(page, normalized)
    if structural is not None:
        if structural.state != CITY_STATE_SELECTED:
            raise LocationControlResolutionError("target_city_not_selected")
        return visible_location or normalized
    if visible_location is not None:
        return visible_location
    raise LocationControlResolutionError("target_city_selection_unverified")


def _visible_native_select(parent_select: Any) -> bool:
    try:
        return parent_select.count() == 1 and _is_presented_in_viewport(parent_select)
    except Exception:
        return False


def _visible_role_matches(
    scope: Any, *, role: str, exact_name: re.Pattern[str]
) -> list[Any]:
    matches = scope.get_by_role(role, name=exact_name)
    visible: list[Any] = []
    for index in range(matches.count()):
        candidate = matches.nth(index)
        try:
            if role == "button" and _has_css_class(candidate, LOCATION_SELECTOR_CLASS):
                continue
            parent_select = candidate.locator("xpath=ancestor::select[1]")
            if role == "option":
                if _visible_native_select(parent_select) or _is_presented_in_viewport(candidate):
                    visible.append(candidate)
            elif _is_presented_in_viewport(candidate):
                visible.append(candidate)
        except Exception:
            continue
    return _collapse_overlapping_presented_items(
        _collapse_nested_presented_items(visible)
    )


def _city_modal_scope(page: Any, exact_name: re.Pattern[str]) -> Any:
    try:
        raw_prompts = _presented_items(page.get_by_text(CITY_MODAL_PROMPT_PATTERN))
    except Exception:
        return page
    if not raw_prompts:
        return page
    prompts = _collapse_overlapping_presented_items(
        _collapse_nested_presented_items(raw_prompts)
    )
    if len(prompts) != 1:
        raise LocationControlResolutionError(
            "target_city_not_unique",
            diagnostic={
                "stage": "prompt",
                "candidate_count": len(raw_prompts),
                "effective_count": len(prompts),
            },
        )

    scope = prompts[0]
    for _ in range(CITY_MODAL_SCOPE_MAX_ANCESTORS):
        try:
            parent = scope.locator("xpath=..")
        except Exception:
            break
        try:
            if parent.count() != 1:
                break
        except Exception:
            break
        for role in CITY_CONTROL_ROLES:
            try:
                if _visible_role_matches(parent, role=role, exact_name=exact_name):
                    return parent
            except Exception:
                continue
        scope = parent
    raise LocationControlResolutionError(
        "target_city_not_found",
        diagnostic={"stage": "prompt_scope", "candidate_count": len(prompts)},
    )


def _usable_labels(options: Any, *, require_visible: bool) -> tuple[str, ...]:
    labels: list[str] = []
    for index in range(options.count()):
        option = options.nth(index)
        if require_visible and not _is_presented_in_viewport(option):
            continue
        label = _label(option)
        if label and label.casefold() not in _IGNORED_CITY_LABELS:
            labels.append(label)
    return tuple(sorted(set(labels), key=str.casefold))


def _option_container_labels(option: Any) -> tuple[str, ...] | None:
    try:
        parent = option.locator("xpath=ancestor::select[1]")
    except Exception:
        parent = None
    if parent is not None and parent.count() == 1:
        return _usable_labels(parent.locator("option"), require_visible=False)
    try:
        listbox = option.locator("xpath=ancestor::*[@role='listbox'][1]")
    except Exception:
        return None
    if listbox.count() != 1:
        return None
    return _usable_labels(listbox.get_by_role("option"), require_visible=True)


def _radio_scope_labels(scope: Any) -> tuple[str, ...] | None:
    try:
        radios = scope.get_by_role("radio")
    except Exception:
        return None
    labels: list[str] = []
    for index in range(radios.count()):
        radio = radios.nth(index)
        if not _is_presented_in_viewport(radio):
            continue
        label = _label(radio)
        if label and label.casefold() not in _IGNORED_CITY_LABELS:
            labels.append(label)
    return tuple(sorted(set(labels), key=str.casefold)) or None


def _resolve_exact_city_control_once(page: Any, normalized: str) -> ResolvedCityControl:
    structural = _structural_city_button(page, normalized)
    if structural is not None:
        return structural

    exact = re.compile(rf"^{re.escape(normalized)}$", re.I)
    scope = _city_modal_scope(page, exact)
    for role in CITY_CONTROL_ROLES:
        candidates = _visible_role_matches(scope, role=role, exact_name=exact)
        if not candidates:
            continue
        if len(candidates) != 1:
            raise LocationControlResolutionError(
                "target_city_not_unique",
                diagnostic={
                    "stage": "role",
                    "role": role,
                    "candidate_count": len(candidates),
                    "effective_count": len(candidates),
                },
            )
        locator = candidates[0]
        if role == "option":
            labels = _option_container_labels(locator)
        elif role == "radio":
            labels = _radio_scope_labels(scope)
        else:
            labels = None
        if not labels:
            labels = (normalized,)
        return ResolvedCityControl(locator=locator, role=role, available_cities=labels)

    raise LocationControlResolutionError(
        "target_city_not_found",
        diagnostic={"stage": "role_scan", "candidate_count": 0},
    )


def _wait_for_next_city_probe(page: Any) -> None:
    try:
        page.wait_for_timeout(CITY_CONTROL_READY_POLL_MS)
    except Exception:
        return


def _retryable_city_resolution_error(exc: LocationControlResolutionError) -> bool:
    """Reintenta sólo ausencia temporal o duplicación transitoria del prompt.

    La captura live puede ver dos prompts del modal antes de que aparezcan los
    botones estructurales de ciudad. Esa ambigüedad de scope no concede permiso para
    elegir ninguno: sólo mantiene abierta la ventana de readiness. Una ambigüedad
    real de controles (`stage=role`) sigue fallando inmediatamente y, si el prompt
    permanece duplicado hasta agotar el deadline, también termina fail-closed.
    """

    if str(exc) == "target_city_not_found":
        return True
    return (
        str(exc) == "target_city_not_unique"
        and exc.diagnostic.get("stage") == "prompt"
    )


def resolve_exact_city_control(page: Any, city_name: str) -> ResolvedCityControl:
    """Resuelve ciudad exacta con readiness acotada y deduplicación visual segura."""

    normalized = _clean_label(city_name)
    if normalized is None:
        raise LocationControlResolutionError("target_city_name_invalid")
    wait_count = CITY_CONTROL_READY_TIMEOUT_MS // CITY_CONTROL_READY_POLL_MS
    for attempt in range(wait_count + 1):
        try:
            return _resolve_exact_city_control_once(page, normalized)
        except LocationControlResolutionError as exc:
            if not _retryable_city_resolution_error(exc) or attempt == wait_count:
                raise
            _wait_for_next_city_probe(page)
    raise AssertionError("unreachable")


def activate_city_control(control: ResolvedCityControl, city_name: str) -> bool:
    if control.state == CITY_STATE_SELECTED:
        return False
    if control.state not in {None, CITY_STATE_UNSELECTED}:
        raise LocationControlResolutionError("target_city_state_invalid")
    try:
        parent = control.locator.locator("xpath=ancestor::select[1]")
    except Exception:
        parent = None
    if parent is not None and parent.count() == 1:
        parent.select_option(label=city_name)
        return True
    control.locator.click()
    return True
