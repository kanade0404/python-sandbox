import sys
sys.path.insert(0, "/Users/kanade0404/work/python-sandbox/.claude/worktrees/replicated-floating-hollerith/sqlacodegen-trial/src")

from models_pg import OrderItems

a = OrderItems.quantity * OrderItems.unit_price
b = OrderItems.unit_price * OrderItems.quantity

print("quantity(Integer) * unit_price(Numeric):", a.type, type(a.type))
print("unit_price(Numeric) * quantity(Integer):", b.type, type(b.type))
