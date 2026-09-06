import type { SavedReportOpenRequest } from "./savedReportOpenProtocol";

interface SavedReportFocusDependencies<TReport, TRun> {
  getReports: () => TReport[];
  loadReports: () => Promise<unknown>;
  openReport: (report: TReport) => Promise<unknown>;
  openRunsTab: () => Promise<unknown>;
  openDetailTab?: (tab: "info" | "runs" | "subscription") => Promise<unknown>;
  getRuns: () => TRun[];
  openRun: (run: TRun) => Promise<unknown>;
}

const hasMatchingId = (value: unknown, expected: string) =>
  String((value as { id?: unknown })?.id ?? "") === expected;

export const focusSavedReportTarget = async <TReport, TRun>(
  request: SavedReportOpenRequest,
  dependencies: SavedReportFocusDependencies<TReport, TRun>,
): Promise<boolean> => {
  let report = dependencies.getReports().find(item => hasMatchingId(item, request.report_id));
  if (!report) {
    await dependencies.loadReports();
    report = dependencies.getReports().find(item => hasMatchingId(item, request.report_id));
  }
  if (!report) return false;

  await dependencies.openReport(report);
  const detailTab = request.detail_tab || "runs";
  if (dependencies.openDetailTab) {
    await dependencies.openDetailTab(detailTab);
  } else {
    await dependencies.openRunsTab();
  }

  if (detailTab === "runs" && request.run_id) {
    const run = dependencies.getRuns().find(item => hasMatchingId(item, request.run_id));
    if (run) await dependencies.openRun(run);
  }
  return true;
};
