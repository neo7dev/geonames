# geonames_server/log/rich_console.py

import inspect
import pprint
import sys
import time
from functools import wraps

from rich.box import HORIZONTALS
from rich.console import Console, Group
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table

from geonames_server.config.config import settings
from geonames_server.log.console import console

ROW_1_STYLE = "blue"
ROW_2_STYLE = "white"
ROW_3_STYLE = "green"


# ✅ expose getter
def get_console() -> Console:
    return console


# ============================================================
# Global filter configuration
# ============================================================

# Keys always allowed even if "__"
ALLOWED_SPECIAL_GLOBALS = {
    "__name__",
    "__file__",
    "__package__",
}


# Keys always excluded
EXCLUDED_GLOBALS = {
    "__builtins__",
    "__loader__",
    "__spec__",
    "__cached__",
    "__annotations__",
    "__doc__",
}


GLOBAL_FILTER_CONFIG = {
    "allowed_special": ALLOWED_SPECIAL_GLOBALS,
    "excluded": EXCLUDED_GLOBALS,
}


def filter_globals(globals_dict: dict) -> dict:
    filtered = {}

    for key, value in globals_dict.items():
        if key in GLOBAL_FILTER_CONFIG["excluded"]:
            continue
        if key.startswith("__"):
            if key in GLOBAL_FILTER_CONFIG["allowed_special"]:
                filtered[key] = value
        else:
            filtered[key] = value

    return filtered


def format_value(v):

    if isinstance(v, (dict, list, set, tuple)):
        return pprint.pformat(v, indent=2, width=80, compact=False)

    try:
        return repr(v)
    except Exception:
        return str(v)


def make_table(title, data):

    table = Table(
        title=title,
        show_header=True,
        header_style="bold cyan",
        show_lines=False,
        box=None,
        expand=True,
    )

    table.add_column("Name", style="green", no_wrap=True)
    table.add_column("Type", style="magenta", no_wrap=True)
    table.add_column("Value", style="white")

    for k, v in data.items():
        table.add_row(
            str(k),
            type(v).__name__,
            format_value(v),
        )

    return table


def make_section(title, data):

    if not data:
        return None

    return Panel(
        make_table(title, data),
        title=f"[bold]{title}[/bold]",
        border_style="cyan",
        expand=True,
    )


def verbose_debug(func):

    sig = inspect.signature(func)
    is_async = inspect.iscoroutinefunction(func)

    def render(
        name, locals_dict, globals_dict, args_dict, kwargs_dict, result, elapsed
    ):

        level = settings.log_level.upper()

        if level not in ("INFO", "DEBUG"):
            return

        sections = []

        sections.append(
            Panel(
                f"[cyan]Execution time:[/cyan] {elapsed:.6f}s",
                border_style="yellow",
                expand=True,
            )
        )

        if locals_dict:
            sections.append(make_section("Locals", locals_dict))

        if args_dict:
            sections.append(make_section("Args", args_dict))

        if kwargs_dict:
            sections.append(make_section("Kwargs", kwargs_dict))

        if level == "DEBUG":
            if globals_dict:
                sections.append(make_section("Globals", globals_dict))

            sections.append(make_section("Return", {"return": result}))

        console.print(
            Panel(
                Group(*sections),
                title=f"[bold yellow]VERBOSE DEBUG → {name}[/bold yellow]",
                border_style="bright_blue",
                expand=True,
            )
        )

    # ========================================================
    # SYNC
    # ========================================================

    @wraps(func)
    def sync_wrapper(*args, **kwargs):

        level = settings.log_level.upper()

        if level not in ("INFO", "DEBUG"):
            return func(*args, **kwargs)

        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()

        args_dict = dict(zip(sig.parameters, args))
        kwargs_dict = kwargs.copy()

        start = time.perf_counter()

        result = func(*args, **kwargs)

        elapsed = time.perf_counter() - start

        locals_dict = bound.arguments.copy()

        globals_dict = filter_globals(func.__globals__)

        render(
            func.__name__,
            locals_dict,
            globals_dict,
            args_dict,
            kwargs_dict,
            result,
            elapsed,
        )

        return result

    # ========================================================
    # ASYNC
    # ========================================================

    @wraps(func)
    async def async_wrapper(*args, **kwargs):

        level = settings.log_level.upper()

        if level not in ("INFO", "DEBUG"):
            return await func(*args, **kwargs)

        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()

        args_dict = dict(zip(sig.parameters, args))
        kwargs_dict = kwargs.copy()

        start = time.perf_counter()

        result = await func(*args, **kwargs)

        elapsed = time.perf_counter() - start

        locals_dict = bound.arguments.copy()

        globals_dict = filter_globals(func.__globals__)

        render(
            func.__name__,
            locals_dict,
            globals_dict,
            args_dict,
            kwargs_dict,
            result,
            elapsed,
        )

        return result

    return async_wrapper if is_async else sync_wrapper


# ============================================================
# MAIN DECORATOR
# ============================================================


def log_variables(func):

    is_async = inspect.iscoroutinefunction(func)

    def capture_and_print(
        args, kwargs, result, elapsed, captured_locals, captured_globals
    ):

        level = settings.log_level.upper()

        if level == "INFO":
            console.print(
                Panel(
                    Pretty(
                        captured_locals,
                        expand_all=True,
                    ),
                    title=f"[INFO] {func.__name__}",
                    border_style="cyan",
                )
            )

        elif level == "DEBUG":
            filtered_globals = filter_globals(captured_globals)

            console.print(
                Panel(
                    Pretty(
                        {
                            "locals": captured_locals,
                            "globals": filtered_globals,
                            "args": args,
                            "kwargs": kwargs,
                            "return": result,
                            "execution_time_sec": round(elapsed, 6),
                        },
                        expand_all=True,
                    ),
                    title=f"[DEBUG TRACE] {func.__name__}",
                    border_style="yellow",
                )
            )

    def tracer(frame, event, arg):

        if frame.f_code == func.__code__ and event == "return":
            tracer.captured_locals = frame.f_locals.copy()
            tracer.captured_globals = frame.f_globals.copy()

        return tracer

    tracer.captured_locals = {}
    tracer.captured_globals = {}

    # =====================
    # SYNC
    # =====================

    @wraps(func)
    def sync_wrapper(*args, **kwargs):

        level = settings.log_level.upper()

        if level not in ("INFO", "DEBUG"):
            return func(*args, **kwargs)

        start = time.perf_counter()

        sys.settrace(tracer)

        try:
            result = func(*args, **kwargs)

        finally:
            sys.settrace(None)

        elapsed = time.perf_counter() - start

        capture_and_print(
            args,
            kwargs,
            result,
            elapsed,
            tracer.captured_locals,
            tracer.captured_globals,
        )

        return result

    # =====================
    # ASYNC
    # =====================

    @wraps(func)
    async def async_wrapper(*args, **kwargs):

        level = settings.log_level.upper()

        if level not in ("INFO", "DEBUG"):
            return await func(*args, **kwargs)

        start = time.perf_counter()

        sys.settrace(tracer)

        try:
            result = await func(*args, **kwargs)

        finally:
            sys.settrace(None)

        elapsed = time.perf_counter() - start

        capture_and_print(
            args,
            kwargs,
            result,
            elapsed,
            tracer.captured_locals,
            tracer.captured_globals,
        )

        return result

    return async_wrapper if is_async else sync_wrapper


def debug_trace(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if settings.log_level == "DEBUG":
            console.rule(f"[bold cyan]Entering {func.__name__}")
            console.log("Arguments:", args, kwargs)
        result = func(*args, **kwargs)
        if settings.log_level == "DEBUG":
            console.log("Result:", result)
            console.rule(f"[bold green]Exiting {func.__name__}")
        return result

    return wrapper


def debug_variables_table(func):
    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if settings.log_level.upper() != "DEBUG":
                return await func(*args, **kwargs)
            captured_locals = {}

            def tracer(frame, event, arg):
                if frame.f_code == func.__code__ and event == "return":
                    captured_locals.update(frame.f_locals)

            start = time.perf_counter()
            sys.setprofile(tracer)
            try:
                result = await func(*args, **kwargs)
            finally:
                sys.setprofile(None)
            elapsed = time.perf_counter() - start
            _print_table(func, args, kwargs, captured_locals, result, elapsed)
            return result

        return async_wrapper
    else:

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if settings.log_level.upper() != "DEBUG":
                return func(*args, **kwargs)
            captured_locals = {}

            def tracer(frame, event, arg):
                if frame.f_code == func.__code__ and event == "return":
                    captured_locals.update(frame.f_locals)

            start = time.perf_counter()
            sys.setprofile(tracer)
            try:
                result = func(*args, **kwargs)
            finally:
                sys.setprofile(None)
            elapsed = time.perf_counter() - start
            _print_table(func, args, kwargs, captured_locals, result, elapsed)
            return result

        return sync_wrapper


def _print_table(func, args, kwargs, captured_locals, result, elapsed):
    table = Table(
        show_header=True,
        box=HORIZONTALS,
        show_edge=False,
        show_lines=False,
        pad_edge=False,
        expand=False,
    )
    table.add_column("Variable", style=ROW_1_STYLE, no_wrap=True)
    table.add_column("Type", style=ROW_2_STYLE, no_wrap=True)
    table.add_column("Value", style=ROW_3_STYLE)

    def add_row(name, val):
        table.add_row(name, type(val).__name__, Pretty(val, expand_all=True))

    if args:
        for i, val in enumerate(args):
            add_row(f"arg[{i}]", val)
    for k, v in kwargs.items():
        add_row(k, v)
    for k, v in captured_locals.items():
        add_row(k, v)
    add_row("RETURN", result)
    table.add_row("EXECUTION_TIME", "float", f"{elapsed:.6f}")
    console.print(
        Panel(
            table,
            title=f"[bold yellow]DEBUG VARIABLES → {func.__name__}[/bold yellow]",
            border_style="yellow",
            expand=True,
        )
    )
