from dotenv import load_dotenv

load_dotenv()

from .cases import CASES
from .implement_this.orchestrator import process_message


def main() -> None:
    passed = 0
    failed: list[int] = []
    for i, case in enumerate(CASES, start=1):
        print(f"=== [{i}] ===")
        for m in case.state.history:
            print(f"  [{m.role:<9}] {m.content}")
        print(f"  [user     ] {case.current_message.content}")

        turn = process_message(case.current_message.content, case.state)

        actual_action = turn.action.name if turn.action is not None else None
        actual = (turn.state.active_intent, turn.state.status, actual_action)
        expected = (case.expected.active_intent, case.expected.status, case.expected.action)
        ok = actual == expected

        print(f"  response: {turn.response}")
        if turn.action is not None:
            print(f"     · tool {turn.action.name}({turn.action.args}) -> {turn.action.result}")
        print(f"     · state.status={turn.state.status} active_intent={turn.state.active_intent}")
        print(f"  [{'PASS' if ok else 'FAIL'}] expected={expected}")
        print()

        if ok:
            passed += 1
        else:
            failed.append(i)

    total = len(CASES)
    print(f"--- {passed}/{total} passed" + (f", failed: {failed}" if failed else "") + " ---")


if __name__ == "__main__":
    main()
