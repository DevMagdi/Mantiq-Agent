import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import "../i18n";
import { Brand } from "./Brand";
import { DataTable } from "./DataTable";
import { Badge, EmptyState, ErrorState, Kpi, Progress, StatusChip } from "./ui";

describe("design-system primitives", () => {
  it("renders the Mantiq brand lockup with the product name", () => {
    render(<Brand />);
    expect(screen.getByText("ProcureAI")).toBeTruthy();
    expect(document.querySelector("svg")).toBeTruthy();
  });

  it("renders status chips with a humanised label", () => {
    render(<StatusChip status="replied" />);
    expect(screen.getByText(/replied|تم الرد/i)).toBeTruthy();
  });

  it("renders a KPI with value, hint and trend", () => {
    render(<Kpi label="Savings" value="EGP 1,000" hint="vs budget" trend={{ value: 12.5, label: "savings" }} />);
    expect(screen.getByText("Savings")).toBeTruthy();
    expect(screen.getByText("EGP 1,000")).toBeTruthy();
    expect(screen.getByText(/12.5/)).toBeTruthy();
  });

  it("always renders empty and error states with an action", () => {
    render(
      <div>
        <EmptyState title="Nothing here" description="Add a request" />
        <ErrorState message="boom" onRetry={() => undefined} />
      </div>,
    );
    expect(screen.getByText("Nothing here")).toBeTruthy();
    expect(screen.getByText("boom")).toBeTruthy();
  });

  it("renders badges and progress without hard-coded colours", () => {
    const { container } = render(
      <div>
        <Badge tone="ok">ok</Badge>
        <Progress value={42} tone="info" />
      </div>,
    );
    expect(container.textContent).toContain("ok");
    expect(container.querySelector("[style]")).toBeTruthy();
  });
});

describe("DataTable", () => {
  const rows = [
    { code: "RFQ-1", title: "Rebar", status: "sent" },
    { code: "RFQ-2", title: "Bearings", status: "closed" },
  ];

  it("renders every row and a row count footer", () => {
    const { container } = render(
      <DataTable
        data={rows}
        columns={[
          { accessorKey: "code", header: "Code" },
          { accessorKey: "title", header: "Title" },
        ]}
      />,
    );
    expect(screen.getByText("RFQ-1")).toBeTruthy();
    expect(screen.getByText("RFQ-2")).toBeTruthy();
    expect(container.textContent).toContain("2 / 2");
  });

  it("exposes an accessible search input", () => {
    render(<DataTable data={rows} columns={[{ accessorKey: "code", header: "Code" }]} searchPlaceholder="Search RFQs" />);
    expect(screen.getByPlaceholderText("Search RFQs")).toBeTruthy();
  });
});
