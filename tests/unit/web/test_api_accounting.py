"""Unit tests for /api/v1/accounting REST endpoints."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from surebet.storage.auth import AuthRepository
from surebet.storage.db import init_db
from surebet.web.app import create_app

FAKE_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def accounting_clients(tmp_path: Path) -> tuple[TestClient, str, TestClient, str, Path]:
    db_path = tmp_path / "test_acc.db"
    init_db(db_path)
    auth = AuthRepository(db_path)
    _, session_a = auth.register_user("alice", FAKE_PASSWORD)
    _, session_b = auth.register_user("bob", FAKE_PASSWORD)

    app = create_app(db_path=db_path)
    client_a = TestClient(app)
    client_a.cookies.set("session_token", session_a.token)
    client_b = TestClient(app)
    client_b.cookies.set("session_token", session_b.token)

    return client_a, session_a.csrf_token, client_b, session_b.csrf_token, db_path


def test_accounting_account_crud_and_isolation(
    accounting_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, client_b, csrf_b, _ = accounting_clients

    # 1. CSRF validation on account creation
    res = client_a.post(
        "/api/v1/accounting/accounts",
        json={
            "account_name": "Source B 1",
            "initial_balance_cents": 500_000,
            "note": "Birincil kasa",
        },
    )
    assert res.status_code == 403

    # 2. Alice creates accounts
    res = client_a.post(
        "/api/v1/accounting/accounts",
        json={
            "account_name": "Source B 1",
            "initial_balance_cents": 500_000,
            "note": "Birincil kasa",
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res.status_code == 200, res.text
    acc1 = res.json()
    assert acc1["account_name"] == "Source B 1"
    assert acc1["initial_balance_cents"] == 500_000
    assert acc1["current_balance_cents"] == 500_000
    assert acc1["total_deposit_cents"] == 0
    assert acc1["total_withdrawal_cents"] == 0
    assert acc1["net_profit_cents"] == 0

    res = client_a.post(
        "/api/v1/accounting/accounts",
        json={
            "account_name": "Source B 2",
            "initial_balance_cents": 200_000,
            "note": "İkincil kasa",
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res.status_code == 200
    acc2 = res.json()
    assert acc2["account_name"] == "Source B 2"

    # 3. User isolation: Bob sees 0 accounts
    res_b = client_b.get("/api/v1/accounting/accounts")
    assert res_b.status_code == 200
    assert res_b.json() == []

    # Alice sees 2 accounts
    res_a = client_a.get("/api/v1/accounting/accounts")
    assert res_a.status_code == 200
    accounts = res_a.json()
    assert len(accounts) == 2
    assert accounts[0]["account_name"] == "Source B 1"
    assert accounts[1]["account_name"] == "Source B 2"

    # 4. Update account
    res_up = client_a.put(
        f"/api/v1/accounting/accounts/{acc1['id']}",
        json={"account_name": "Source B Ana", "note": "Güncellenmiş not"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res_up.status_code == 200
    assert res_up.json()["account_name"] == "Source B Ana"
    assert res_up.json()["note"] == "Güncellenmiş not"

    # Bob cannot update Alice's account
    res_b_up = client_b.put(
        f"/api/v1/accounting/accounts/{acc1['id']}",
        json={"account_name": "Hacked"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert res_b_up.status_code == 404


def test_accounting_transactions_and_summary(
    accounting_clients: tuple[TestClient, str, TestClient, str, Path],
) -> None:
    client_a, csrf_a, client_b, csrf_b, _ = accounting_clients

    # Create account
    res = client_a.post(
        "/api/v1/accounting/accounts",
        json={"account_name": "Source B VIP", "initial_balance_cents": 100_000},
        headers={"X-CSRF-Token": csrf_a},
    )
    acc_id = res.json()["id"]

    # 1. Deposit 500 TL (50_000 cents) on Source B VIP
    res_tx1 = client_a.post(
        "/api/v1/accounting/transactions",
        json={
            "tx_type": "DEPOSIT",
            "amount_cents": 50_000,
            "tx_date": "2026-09-20",
            "note": "İlk yatırım",
            "account_id": acc_id,
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res_tx1.status_code == 200, res_tx1.text
    tx1 = res_tx1.json()
    assert tx1["account_id"] == acc_id
    assert tx1["amount_cents"] == 50_000
    assert tx1["status"] == "ACTIVE"

    # 2. Withdrawal 800 TL (80_000 cents) on Source B VIP
    res_tx2 = client_a.post(
        "/api/v1/accounting/transactions",
        json={
            "tx_type": "WITHDRAWAL",
            "amount_cents": 80_000,
            "tx_date": "2026-09-20",
            "note": "Kazanç çekim",
            "account_id": acc_id,
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res_tx2.status_code == 200
    tx2 = res_tx2.json()

    # 3. Check account balance:
    # initial 100_000 + deposit 50_000 - withdrawal 80_000 = 70_000
    # net_profit: 80_000 - 50_000 = +30_000
    res_acc = client_a.get("/api/v1/accounting/accounts")
    accounts = res_acc.json()
    assert accounts[0]["current_balance_cents"] == 70_000
    assert accounts[0]["net_profit_cents"] == 30_000

    # 4. Check summary endpoint
    res_sum = client_a.get("/api/v1/accounting/summary?date=2026-09-20")
    assert res_sum.status_code == 200
    summary = res_sum.json()
    assert summary["daily"]["total_deposit_cents"] == 50_000
    assert summary["daily"]["total_withdrawal_cents"] == 80_000
    assert summary["daily"]["net_profit_cents"] == 30_000
    assert len(summary["daily"]["account_breakdowns"]) == 1
    assert summary["daily"]["account_breakdowns"][0]["account_id"] == acc_id
    assert summary["daily"]["account_breakdowns"][0]["net_profit_cents"] == 30_000

    # 5. List transactions endpoint
    res_txs = client_a.get(f"/api/v1/accounting/transactions?account_id={acc_id}")
    assert res_txs.status_code == 200
    txs = res_txs.json()
    assert len(txs) == 2

    # 6. Cancel transaction
    res_cancel = client_a.post(
        f"/api/v1/accounting/transactions/{tx2['id']}/cancel",
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "CANCELLED"

    # Check updated balance: only deposit is active -> balance = 100_000 + 50_000 = 150_000
    res_acc_after = client_a.get("/api/v1/accounting/accounts")
    assert res_acc_after.json()[0]["current_balance_cents"] == 150_000
    assert res_acc_after.json()[0]["net_profit_cents"] == -50_000

    # 7. Bob cannot cancel Alice's transaction
    res_b_cancel = client_b.post(
        f"/api/v1/accounting/transactions/{tx1['id']}/cancel",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert res_b_cancel.status_code == 404

    # 8. Delete account
    res_del = client_a.delete(
        f"/api/v1/accounting/accounts/{acc_id}",
        headers={"X-CSRF-Token": csrf_a},
    )
    assert res_del.status_code == 200
    res_acc_final = client_a.get("/api/v1/accounting/accounts")
    assert len(res_acc_final.json()) == 0
