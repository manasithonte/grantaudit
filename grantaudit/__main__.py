"""CLI: python -m grantaudit AWARD.txt|pdf LEDGER.csv [--json]"""
import sys
from pathlib import Path

from .parsers import parse_award, parse_ledger_csv, pdf_to_text
from .reconciler import audit


def main(argv=None) -> int:
    from . import llm
    llm.load_dotenv()
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) != 2:
        print(__doc__)
        return 2
    award_path, ledger_path = map(Path, args)
    text = pdf_to_text(str(award_path)) if award_path.suffix.lower() == ".pdf" else award_path.read_text()
    rows, summary, errors = parse_ledger_csv(ledger_path.read_text())
    d = audit(parse_award(text), rows, summary, ledger_errors=errors)
    if as_json:
        print(d.model_dump_json(indent=2))
        return 0
    print(f"{d.status}: {d.message}")
    if d.status == "CANNOT_ASSESS":
        for e in d.errors:
            print(f"  - {e}")
        return 1
    print(f"  total direct {d.total_direct:,}  MTDC {d.mtdc:,}  computed F&A {d.computed_fa:,}  charged F&A "
          f"{'-' if d.charged_fa is None else f'{d.charged_fa:,}'}")
    for v in d.violations:
        print(f"  [{v.severity.value}] {v.type.value} delta={v.delta:,} txns={','.join(v.affected_txn_ids) or '-'} ({v.rule_ref})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
