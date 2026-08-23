import sys
sys.path.insert(0, "/Users/kanade0404/work/python-sandbox/.claude/worktrees/replicated-floating-hollerith/sqlacodegen-trial/src")
from models_pg import OrderItems

a = OrderItems.quantity * OrderItems.unit_price
reveal_type(a)
b = OrderItems.unit_price * OrderItems.quantity
reveal_type(b)
