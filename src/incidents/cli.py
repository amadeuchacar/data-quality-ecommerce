"""
Incident command line - the human side of the incident process.

Detection is automatic (checks runner); investigation and resolution are not. This CLI is
how a person closes the loop, producing the data behind TTR, certification and the dashboard:

    list         open incidents (or --all)
    resolve      register the resolution of an incident (computes TTR)
    rca          record a 5 Whys root cause analysis
    postmortem   render the blameless postmortem of an incident to Markdown

Incident ids can be abbreviated to any unique prefix (like git commit hashes).

Usage:
    poetry run python -m src.incidents.cli list
    poetry run python -m src.incidents.cli resolve 662dd7b2 --summary "Upstream export re-run"
    poetry run python -m src.incidents.cli rca 662dd7b2 --why "..." --why "..." --root-cause "..." --by "Amadeu"
    poetry run python -m src.incidents.cli postmortem 662dd7b2 --impact "..." --timeline "..." --action "..."
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT, load_settings
from src.incidents.incident_log import load_incidents, load_resolutions, open_incidents, register_resolution
from src.incidents.rca import RCA, load_rca_for_incident, render_postmortem, save_postmortem, save_rca


def find_by_prefix(prefix: str, incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """Returns the single incident whose id starts with `prefix`."""
    matches = [inc for inc in incidents if inc["incident_id"].startswith(prefix)]
    if not matches:
        raise ValueError(f"No incident id starts with '{prefix}'.")
    if len(matches) > 1:
        raise ValueError(f"Prefix '{prefix}' is ambiguous ({len(matches)} incidents) - type more characters.")
    return matches[0]


def _display(path: Path) -> str:
    """Path relative to the project when possible (shorter), absolute otherwise."""
    return str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path)


def _cmd_list(args: argparse.Namespace, incidents_dir: Path) -> None:
    incidents = load_incidents(incidents_dir)
    shown = incidents if args.all else open_incidents(incidents, load_resolutions(incidents_dir))
    if not shown:
        print("No incidents." if args.all else "No open incidents.")
        return
    for inc in shown:
        print(
            f"{inc['incident_id'][:8]}  {inc.get('severity', '-'):<8}  {inc['table'] + '/' + inc['pillar']:<28}  "
            f"detected {inc['detected_at'][:19]}  {inc['detail']}"
        )


def _cmd_resolve(args: argparse.Namespace, incidents_dir: Path) -> None:
    incident = find_by_prefix(args.incident, load_incidents(incidents_dir))
    resolution = register_resolution(incident["incident_id"], args.summary, incidents_dir)
    print(f"Resolved {incident['incident_id']} - TTR {resolution.ttr_seconds / 3600:.2f}h.")


def _cmd_rca(args: argparse.Namespace, incidents_dir: Path) -> None:
    incident = find_by_prefix(args.incident, load_incidents(incidents_dir))
    save_rca(RCA(incident["incident_id"], args.why, args.root_cause, args.by), incidents_dir)
    print(f"RCA recorded for {incident['incident_id']} ({len(args.why)} whys).")


def _cmd_postmortem(args: argparse.Namespace, incidents_dir: Path) -> None:
    incident = find_by_prefix(args.incident, load_incidents(incidents_dir))
    rca = load_rca_for_incident(incident["incident_id"], incidents_dir)
    if rca is None:
        raise ValueError("Record the RCA first (the postmortem includes the 5 Whys).")
    markdown = render_postmortem(incident, rca, args.timeline, args.impact, args.action)
    path = save_postmortem(incident["incident_id"], markdown, incidents_dir)
    print(f"Postmortem saved to: {_display(path)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.incidents.cli", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list open incidents")
    p_list.add_argument("--all", action="store_true", help="include resolved incidents")
    p_list.set_defaults(func=_cmd_list)

    p_resolve = sub.add_parser("resolve", help="register the resolution of an incident")
    p_resolve.add_argument("incident", help="incident id or unique prefix")
    p_resolve.add_argument("--summary", required=True, help="what was done to fix it")
    p_resolve.set_defaults(func=_cmd_resolve)

    p_rca = sub.add_parser("rca", help="record a 5 Whys root cause analysis")
    p_rca.add_argument("incident", help="incident id or unique prefix")
    p_rca.add_argument("--why", action="append", required=True, help="one answer per 'why' (repeat the flag)")
    p_rca.add_argument("--root-cause", required=True)
    p_rca.add_argument("--by", required=True, help="who conducted the investigation")
    p_rca.set_defaults(func=_cmd_rca)

    p_pm = sub.add_parser("postmortem", help="render the blameless postmortem to Markdown")
    p_pm.add_argument("incident", help="incident id or unique prefix")
    p_pm.add_argument("--impact", required=True)
    p_pm.add_argument("--timeline", action="append", default=[], help="one event per flag")
    p_pm.add_argument("--action", action="append", default=[], help="one corrective action per flag")
    p_pm.set_defaults(func=_cmd_postmortem)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    incidents_dir = PROJECT_ROOT / load_settings()["incidents_path"]
    try:
        args.func(args, incidents_dir)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
