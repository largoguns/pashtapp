"""Modelo de datos relacional de PashtAPP (DEFINITION.md §3).

Además de las tablas de la especificación se añaden:
  * ``app_settings``: clave/valor (saldo inicial configurado, etc.).
  * ``month_openings``: meses ya abiertos (§4.3), para no regenerar fijos borrados a mano.
  * ``transactions.template_id`` / ``transactions.loan_installment_id``: enlazan los
    movimientos generados en la apertura de mes con su origen.
  * ``transactions.period_year`` / ``period_month``: mes contable al que pertenece el
    movimiento (como la hoja del Excel en la que se apuntaba). Es independiente de la
    fecha: el mes empieza al cobrar el salario, no el día 1.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    icon: Mapped[str] = mapped_column(Text, default="tag", server_default="tag")
    color_hex: Mapped[str] = mapped_column(Text, default="#64748b", server_default="#64748b")
    is_fixed_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    monthly_budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)


class RecurringTemplate(Base):
    __tablename__ = "recurring_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    default_amount: Mapped[float] = mapped_column(Float, nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_income: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    category: Mapped[Category | None] = relationship(lazy="joined")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)  # + ingreso / - gasto
    is_income: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_settled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    installment_group_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    installment_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    installment_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_templates.id", ondelete="SET NULL"), nullable=True
    )
    loan_installment_id: Mapped[int | None] = mapped_column(
        ForeignKey("loan_installments.id", ondelete="SET NULL"), nullable=True
    )

    category: Mapped[Category | None] = relationship(lazy="joined")

    @property
    def booking_date(self) -> date:
        """Fecha real de cargo en el banco (o la de operación si no hay)."""
        return self.settlement_date or self.date

    @property
    def period(self) -> tuple[int, int]:
        return self.period_year, self.period_month


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    annual_interest_rate: Mapped[float] = mapped_column(Float, nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    monthly_fee: Mapped[float] = mapped_column(Float, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    installments: Mapped[list[LoanInstallment]] = relationship(
        back_populates="loan",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LoanInstallment.installment_number",
    )


class LoanInstallment(Base):
    __tablename__ = "loan_installments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id", ondelete="CASCADE"), index=True)
    installment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payment_amount: Mapped[float] = mapped_column(Float, nullable=False)
    capital_amount: Mapped[float] = mapped_column(Float, nullable=False)
    interest_amount: Mapped[float] = mapped_column(Float, nullable=False)
    remaining_capital: Mapped[float] = mapped_column(Float, nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    loan: Mapped[Loan] = relationship(back_populates="installments")


class UtilityReading(Base):
    __tablename__ = "utility_readings"
    __table_args__ = (UniqueConstraint("year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class MonthOpening(Base):
    __tablename__ = "month_openings"
    __table_args__ = (UniqueConstraint("year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
