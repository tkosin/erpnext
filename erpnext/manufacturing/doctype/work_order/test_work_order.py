# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


from __future__ import unicode_literals
import unittest
import frappe
from frappe.utils import flt, time_diff_in_hours, now, add_days, cint
from erpnext.stock.doctype.purchase_receipt.test_purchase_receipt import set_perpetual_inventory
from erpnext.manufacturing.doctype.work_order.work_order \
	import make_stock_entry, ItemHasVariantError, stop_unstop, StockOverProductionError, OverProductionError
from erpnext.stock.doctype.stock_entry import test_stock_entry
from erpnext.stock.utils import get_bin
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order

class TestWorkOrder(unittest.TestCase):
	def setUp(self):
		self.warehouse = '_Test Warehouse 2 - _TC'
		self.item = '_Test Item'

	def check_planned_qty(self):
		set_perpetual_inventory(0)

		planned0 = frappe.db.get_value("Bin", {"item_code": "_Test FG Item",
			"warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty") or 0

		wo_order = make_wo_order_test_record()

		planned1 = frappe.db.get_value("Bin", {"item_code": "_Test FG Item",
			"warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty")

		self.assertEqual(planned1, planned0 + 10)

		# add raw materials to stores
		test_stock_entry.make_stock_entry(item_code="_Test Item",
			target="Stores - _TC", qty=100, basic_rate=100)
		test_stock_entry.make_stock_entry(item_code="_Test Item Home Desktop 100",
			target="Stores - _TC", qty=100, basic_rate=100)

		# from stores to wip
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 4))
		for d in s.get("items"):
			d.s_warehouse = "Stores - _TC"
		s.insert()
		s.submit()

		# from wip to fg
		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 4))
		s.insert()
		s.submit()

		self.assertEqual(frappe.db.get_value("Work Order", wo_order.name, "produced_qty"), 4)

		planned2 = frappe.db.get_value("Bin", {"item_code": "_Test FG Item",
			"warehouse": "_Test Warehouse 1 - _TC"}, "planned_qty")

		self.assertEqual(planned2, planned0 + 6)

		return wo_order

	def test_over_production(self):
		wo_doc = self.check_planned_qty()

		test_stock_entry.make_stock_entry(item_code="_Test Item",
			target="_Test Warehouse - _TC", qty=100, basic_rate=100)
		test_stock_entry.make_stock_entry(item_code="_Test Item Home Desktop 100",
			target="_Test Warehouse - _TC", qty=100, basic_rate=100)

		s = frappe.get_doc(make_stock_entry(wo_doc.name, "Manufacture", 7))
		s.insert()

		self.assertRaises(StockOverProductionError, s.submit)

	def test_make_time_sheet(self):
		from erpnext.manufacturing.doctype.work_order.work_order import make_timesheet
		wo_order = make_wo_order_test_record(item="_Test FG Item 2",
			planned_start_date=now(), qty=1, do_not_save=True)

		wo_order.set_work_order_operations()
		wo_order.insert()
		wo_order.submit()

		d = wo_order.operations[0]
		d.completed_qty = flt(d.completed_qty)

		name = frappe.db.get_value('Timesheet', {'work_order': wo_order.name}, 'name')
		time_sheet_doc = frappe.get_doc('Timesheet', name)
		self.assertEqual(wo_order.company, time_sheet_doc.company)
		time_sheet_doc.submit()

		self.assertEqual(wo_order.name, time_sheet_doc.work_order)
		self.assertEqual((wo_order.qty - d.completed_qty),
			sum([d.completed_qty for d in time_sheet_doc.time_logs]))

		manufacturing_settings = frappe.get_doc({
			"doctype": "Manufacturing Settings",
			"allow_production_on_holidays": 0
		})

		manufacturing_settings.save()

		wo_order.load_from_db()
		self.assertEqual(wo_order.operations[0].status, "Completed")
		self.assertEqual(wo_order.operations[0].completed_qty, wo_order.qty)

		self.assertEqual(wo_order.operations[0].actual_operation_time, 60)
		self.assertEqual(wo_order.operations[0].actual_operating_cost, 6000)

		time_sheet_doc1 = make_timesheet(wo_order.name, wo_order.company)
		self.assertEqual(len(time_sheet_doc1.get('time_logs')), 0)

		time_sheet_doc.cancel()

		wo_order.load_from_db()
		self.assertEqual(wo_order.operations[0].status, "Pending")
		self.assertEqual(flt(wo_order.operations[0].completed_qty), 0)

		self.assertEqual(flt(wo_order.operations[0].actual_operation_time), 0)
		self.assertEqual(flt(wo_order.operations[0].actual_operating_cost), 0)

	def test_planned_operating_cost(self):
		wo_order = make_wo_order_test_record(item="_Test FG Item 2",
			planned_start_date=now(), qty=1, do_not_save=True)
		wo_order.set_work_order_operations()
		cost = wo_order.planned_operating_cost
		wo_order.qty = 2
		wo_order.set_work_order_operations()
		self.assertEqual(wo_order.planned_operating_cost, cost*2)

	def test_production_item(self):
		wo_order = make_wo_order_test_record(item="_Test FG Item", qty=1, do_not_save=True)
		frappe.db.set_value("Item", "_Test FG Item", "end_of_life", "2000-1-1")

		self.assertRaises(frappe.ValidationError, wo_order.save)

		frappe.db.set_value("Item", "_Test FG Item", "end_of_life", None)
		frappe.db.set_value("Item", "_Test FG Item", "disabled", 1)

		self.assertRaises(frappe.ValidationError, wo_order.save)

		frappe.db.set_value("Item", "_Test FG Item", "disabled", 0)

		wo_order = make_wo_order_test_record(item="_Test Variant Item", qty=1, do_not_save=True)
		self.assertRaises(ItemHasVariantError, wo_order.save)

	def test_reserved_qty_for_production_submit(self):
		self.bin1_at_start = get_bin(self.item, self.warehouse)

		# reset to correct value
		self.bin1_at_start.update_reserved_qty_for_production()

		self.wo_order = make_wo_order_test_record(item="_Test FG Item", qty=2,
			source_warehouse=self.warehouse)

		self.bin1_on_submit = get_bin(self.item, self.warehouse)

		# reserved qty for production is updated
		self.assertEqual(cint(self.bin1_at_start.reserved_qty_for_production) + 2,
			cint(self.bin1_on_submit.reserved_qty_for_production))
		self.assertEqual(cint(self.bin1_at_start.projected_qty),
			cint(self.bin1_on_submit.projected_qty) + 2)

	def test_reserved_qty_for_production_cancel(self):
		self.test_reserved_qty_for_production_submit()

		self.wo_order.cancel()

		bin1_on_cancel = get_bin(self.item, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(cint(self.bin1_at_start.reserved_qty_for_production),
			cint(bin1_on_cancel.reserved_qty_for_production))
		self.assertEqual(self.bin1_at_start.projected_qty,
			cint(bin1_on_cancel.projected_qty))

	def test_reserved_qty_for_production_on_stock_entry(self):
		test_stock_entry.make_stock_entry(item_code="_Test Item",
			target= self.warehouse, qty=100, basic_rate=100)
		test_stock_entry.make_stock_entry(item_code="_Test Item Home Desktop 100",
			target= self.warehouse, qty=100, basic_rate=100)

		self.test_reserved_qty_for_production_submit()

		s = frappe.get_doc(make_stock_entry(self.wo_order.name,
			"Material Transfer for Manufacture", 2))

		s.submit()

		bin1_on_start_production = get_bin(self.item, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(cint(self.bin1_at_start.reserved_qty_for_production),
			cint(bin1_on_start_production.reserved_qty_for_production))

		# projected qty will now be 2 less (becuase of item movement)
		self.assertEqual(cint(self.bin1_at_start.projected_qty),
			cint(bin1_on_start_production.projected_qty) + 2)

		s = frappe.get_doc(make_stock_entry(self.wo_order.name, "Manufacture", 2))

		bin1_on_end_production = get_bin(self.item, self.warehouse)

		# no change in reserved / projected
		self.assertEqual(cint(bin1_on_end_production.reserved_qty_for_production),
			cint(bin1_on_start_production.reserved_qty_for_production))
		self.assertEqual(cint(bin1_on_end_production.projected_qty),
			cint(bin1_on_end_production.projected_qty))

	def test_reserved_qty_for_stopped_production(self):
		test_stock_entry.make_stock_entry(item_code="_Test Item",
			target= self.warehouse, qty=100, basic_rate=100)
		test_stock_entry.make_stock_entry(item_code="_Test Item Home Desktop 100",
			target= self.warehouse, qty=100, basic_rate=100)

		# 	0 0 0

		self.test_reserved_qty_for_production_submit()

		#2 0 -2

		s = frappe.get_doc(make_stock_entry(self.wo_order.name,
			"Material Transfer for Manufacture", 1))

		s.submit()

		#1 -1 0

		bin1_on_start_production = get_bin(self.item, self.warehouse)

		# reserved_qty_for_producion updated
		self.assertEqual(cint(self.bin1_at_start.reserved_qty_for_production) + 1,
			cint(bin1_on_start_production.reserved_qty_for_production))

		# projected qty will now be 2 less (becuase of item movement)
		self.assertEqual(cint(self.bin1_at_start.projected_qty),
			cint(bin1_on_start_production.projected_qty) + 2)

		# STOP
		stop_unstop(self.wo_order.name, "Stopped")

		bin1_on_stop_production = get_bin(self.item, self.warehouse)

		# no change in reserved / projected
		self.assertEqual(cint(bin1_on_stop_production.reserved_qty_for_production),
			cint(self.bin1_at_start.reserved_qty_for_production))
		self.assertEqual(cint(bin1_on_stop_production.projected_qty) + 1,
			cint(self.bin1_at_start.projected_qty))

	def test_scrap_material_qty(self):
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=2)

		# add raw materials to stores
		test_stock_entry.make_stock_entry(item_code="_Test Item",
			target="Stores - _TC", qty=10, basic_rate=5000.0)
		test_stock_entry.make_stock_entry(item_code="_Test Item Home Desktop 100",
			target="Stores - _TC", qty=10, basic_rate=1000.0)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 2))
		for d in s.get("items"):
			d.s_warehouse = "Stores - _TC"
		s.insert()
		s.submit()

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Manufacture", 2))
		s.insert()
		s.submit()

		wo_order_details = frappe.db.get_value("Work Order", wo_order.name,
			["scrap_warehouse", "qty", "produced_qty", "bom_no"], as_dict=1)

		scrap_item_details = get_scrap_item_details(wo_order_details.bom_no)

		self.assertEqual(wo_order_details.produced_qty, 2)

		for item in s.items:
			if item.bom_no and item.item_code in scrap_item_details:
				self.assertEqual(wo_order_details.scrap_warehouse, item.t_warehouse)
				self.assertEqual(flt(wo_order_details.qty)*flt(scrap_item_details[item.item_code]), item.qty)

	def test_allow_overproduction(self):
		allow_overproduction("overproduction_percentage_for_work_order", 0)
		wo_order = make_wo_order_test_record(planned_start_date=now(), qty=2)
		test_stock_entry.make_stock_entry(item_code="_Test Item",
			target="_Test Warehouse - _TC", qty=10, basic_rate=5000.0)
		test_stock_entry.make_stock_entry(item_code="_Test Item Home Desktop 100",
			target="_Test Warehouse - _TC", qty=10, basic_rate=1000.0)

		s = frappe.get_doc(make_stock_entry(wo_order.name, "Material Transfer for Manufacture", 3))
		s.insert()
		self.assertRaises(StockOverProductionError, s.submit)

		allow_overproduction("overproduction_percentage_for_work_order", 50)
		s.load_from_db()
		s.submit()
		self.assertEqual(s.docstatus, 1)

		allow_overproduction("overproduction_percentage_for_work_order", 0)

	def test_over_production_for_sales_order(self):
		so = make_sales_order(item_code="_Test FG Item", qty=2)

		allow_overproduction("overproduction_percentage_for_sales_order", 0)
		wo_order = make_wo_order_test_record(planned_start_date=now(),
			sales_order=so.name, qty=3, do_not_save=True)

		self.assertRaises(OverProductionError, wo_order.save)

		allow_overproduction("overproduction_percentage_for_sales_order", 50)
		wo_order = make_wo_order_test_record(planned_start_date=now(),
			sales_order=so.name, qty=3)

		wo_order.submit()
		self.assertEqual(wo_order.docstatus, 1)

		allow_overproduction("overproduction_percentage_for_sales_order", 0)

def get_scrap_item_details(bom_no):
	scrap_items = {}
	for item in frappe.db.sql("""select item_code, stock_qty from `tabBOM Scrap Item`
		where parent = %s""", bom_no, as_dict=1):
		scrap_items[item.item_code] = item.stock_qty

	return scrap_items

def allow_overproduction(fieldname, percentage):
	doc = frappe.get_doc("Manufacturing Settings")
	doc.update({
		fieldname: percentage
	})
	doc.save()

def make_wo_order_test_record(**args):
	args = frappe._dict(args)

	wo_order = frappe.new_doc("Work Order")
	wo_order.production_item = args.production_item or args.item or args.item_code or "_Test FG Item"
	wo_order.bom_no = frappe.db.get_value("BOM", {"item": wo_order.production_item,
		"is_active": 1, "is_default": 1})
	wo_order.qty = args.qty or 10
	wo_order.wip_warehouse = args.wip_warehouse or "_Test Warehouse - _TC"
	wo_order.fg_warehouse = args.fg_warehouse or "_Test Warehouse 1 - _TC"
	wo_order.scrap_warehouse = args.fg_warehouse or "_Test Scrap Warehouse - _TC"
	wo_order.company = args.company or "_Test Company"
	wo_order.stock_uom = args.stock_uom or "_Test UOM"
	wo_order.use_multi_level_bom=0
	wo_order.skip_transfer=1
	wo_order.get_items_and_operations_from_bom()
	wo_order.sales_order = args.sales_order or None

	if args.source_warehouse:
		for item in wo_order.get("required_items"):
			item.source_warehouse = args.source_warehouse

	if args.planned_start_date:
		wo_order.planned_start_date = args.planned_start_date

	if not args.do_not_save:
		wo_order.insert()

		if not args.do_not_submit:
			wo_order.submit()
	return wo_order

test_records = frappe.get_test_records('Work Order')
