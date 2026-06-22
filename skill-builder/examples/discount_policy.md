# Discount Approval Policy

Source: discount_policy
Version: 2026.01

## 1. Purpose

This policy governs how sales discounts are approved across all regions. It
applies to every quote and order created through the sales workflow.

## 2. Definitions

A "discount" means any reduction from list price expressed as a percentage. The
"deal owner" refers to the sales representative who created the quote.

## 3.1 Discount Thresholds

Discounts up to 10% may be applied by any sales manager without further approval.

Discounts above 15% require approval from the VP of Sales.

Discounts above 25% require approval from the Chief Financial Officer.

## 3.2 Regional Restrictions

Customers in the Restricted region must not receive any discount above 5%.

## 4. Data Access

The unit_cost and margin fields must not be returned to sales representatives;
they are restricted to Finance and pricing-entitled roles.

## 5. Audit

Every discount above 10% must be recorded in the decision trace with the
approving role and the source policy clause.
