export default function ImportWizardStepper({ activeStep }) {
  const steps = [
    ["select", "选择 PDF"],
    ["preflight", "导入前预检"],
    ["confirm", "确认入库"],
    ["progress", "导入进度"],
    ["complete", "完成"],
  ];
  const activeIndex = Math.max(0, steps.findIndex(([value]) => value === activeStep));
  return (
    <nav className="linearImportStepper" aria-label="PDF 入库步骤">
      {steps.map(([value, label], index) => (
        <span key={value} className={index === activeIndex ? "active" : index < activeIndex ? "done" : ""}>
          <strong>{index + 1}</strong>
          {label}
        </span>
      ))}
    </nav>
  );
}
