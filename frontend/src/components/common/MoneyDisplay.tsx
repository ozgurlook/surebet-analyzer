import React from "react";
import clsx from "clsx";

interface MoneyDisplayProps {
  cents: number;
  showSign?: boolean;
  className?: string;
  currency?: string;
  size?: "sm" | "md" | "lg" | "xl";
}

export const MoneyDisplay: React.FC<MoneyDisplayProps> = ({
  cents,
  showSign = false,
  className,
  currency = "TL",
  size = "md",
}) => {
  const isPositive = cents > 0;
  const isNegative = cents < 0;
  const absLiras = (Math.abs(cents) / 100).toLocaleString("tr-TR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const sign = showSign && isPositive ? "+" : isNegative ? "-" : "";

  const sizeClasses = {
    sm: "text-xs font-medium",
    md: "text-sm font-semibold",
    lg: "text-base font-bold",
    xl: "text-xl font-bold tracking-tight",
  };

  return (
    <span
      className={clsx(
        "font-mono tabular-nums inline-flex items-baseline gap-0.5",
        sizeClasses[size],
        className
      )}
    >
      <span>
        {sign}
        {absLiras}
      </span>
      <span className="text-[0.8em] font-normal opacity-80">{currency}</span>
    </span>
  );
};
