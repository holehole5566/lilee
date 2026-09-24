import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, Schedule, errorText } from './api';

@Component({
  selector: 'app-guide', standalone: true, imports: [RouterLink],
  template: `
    <h1>操作手冊與衝突展示</h1>
    <p>這是手動情境（Manual）的操作範例；Auto 是另一份替代班表。請先在<strong>可丟棄且空白的 Manual 班表</strong>練習，案例之間刪掉示範 service。這頁只讀取設定，不會新增或清除資料。</p>
    @if (error) { <p role="alert" class="error">{{ error }}</p> }
    @if (schedule) {
      <p>目前 Manual revision {{ schedule.revision }}、{{ schedule.timelines.length }} 筆 service；情境起點（UTC）：{{ schedule.scenario_start_at }}。</p>
      @if (schedule.timelines.length) {
        <p class="error">Manual 並非空白；既有 service 可能增加額外衝突，以下「預期結果」就不一定是唯一的衝突。請勿為了測試刪除要保留的資料。</p>
      }
      <h2>基本操作</h2>
      <ol>
        <li>到 <a routerLink="/editor">Schedule Editor</a> 的 Vehicles 新增兩輛測試車，例如 <code>DEMO_A</code>、<code>DEMO_B</code>；不要使用已被其他 service 引用的車號。</li>
        <li>選車、輸入 Start (UTC)，在地圖點虛線圈 node 或用 Next node 下拉選單依序建立 path。下方列表可以設定 platform dwell 與 Yard 停留充電時間；以下案例都用 0 秒。</li>
        <li>先按 <strong>Preview change</strong> 看推導的到離站、status/conflicts（預覽不寫入），再按 Create。到 <a routerLink="/viewer">Viewer</a> 看完整班表與播放。</li>
        <li>兩輛車不能在同一個 block 或 interlocking group 同時占用；業務衝突可以保存為 <code>draft</code>，但 draft 不是可執行班表。修改或刪除後會重驗整份 Manual 班表。</li>
      </ol>
      <p>下列時間以<strong>目前情境起點和 B1 duration</strong>計算。若重複測試同一案例，請先清理上一次的示範 service；不要刪除其他人的班次。車資與真實鐵路安全間隔不在本模擬範圍。</p>
      <section><h2>案例 1：同一 block 時間重疊</h2>
        <ol>
          <li>DEMO_A：Start <code>{{ origin }}</code>，path <code>Y → B1 → P1A</code>。</li>
          <li>DEMO_B：Start <code>{{ origin }}</code>，path <code>Y → B1 → P1A</code>。</li>
        </ol>
        <p>預期至少有 <code>BLOCK_OVERLAP</code>；B1 同時也屬 G1，所以還會有 <code>INTERLOCKING_OVERLAP</code>。block 占用是半開區間 [start, end)：相同時刻進入會衝突，前車離開瞬間後車進入則不衝突。</p>
      </section>
      <section><h2>案例 2：不同 block、同 interlocking group</h2>
        <ol>
          <li>DEMO_A：Start <code>{{ origin }}</code>，path <code>Y → B1 → P1A</code>。</li>
          <li>DEMO_B：Start <code>{{ origin }}</code>，path <code>Y → B2 → P1B</code>。</li>
        </ol>
        <p>預期有 <code>INTERLOCKING_OVERLAP</code>（B1/B2 都在 G1），但沒有這兩班之間的 <code>BLOCK_OVERLAP</code>。</p>
      </section>
      <section><h2>案例 3：同車兩班時間重疊</h2>
        <ol>
          <li>DEMO_A 第一班：Start <code>{{ origin }}</code>，path <code>Y → B1 → P1A</code>。</li>
          <li>DEMO_A 第二班：Start <code>{{ origin }}</code>，path <code>P1A → B3 → B5 → P2A</code>。</li>
        </ol>
        <p>第二班起點與第一班終點相接，但在第一班完成前出發；預期 <code>VEHICLE_OVERLAP</code>，第二班及後續該車電量顯示 unknown，而不是推造一條軌跡。</p>
      </section>
      <section><h2>案例 4：同車位置不連續</h2>
        <ol>
          <li>DEMO_A 第一班：Start <code>{{ origin }}</code>，path <code>Y → B1 → P1A</code>；預設 dwell=0 時約於 <code>{{ afterB1 }}</code> 抵達 P1A。</li>
          <li>DEMO_A 第二班：Start <code>{{ afterB1 }}</code>，path <code>P2A → B6 → B7 → P3A</code>。</li>
        </ol>
        <p>時間可以銜接，但車不會從 P1A 瞬移到 P2A；預期 <code>LOCATION_DISCONTINUITY</code>。修改第二班起點並補上合法行駛 path 才能修復。</p>
      </section>
      <section><h2>補充：Yard 電量（進階）</h2>
        <p>後端也會檢查 <code>INSUFFICIENT_CHARGE</code>（離開 Y 時低於 80）及 <code>LOW_BATTERY</code>（在 block 中低於 30）。這兩項有 domain tests，但尚未記錄為人工 UI E2E 通過。要測前者，可讓 DEMO_A 先跑 <code>Y → B1 → P1A → B1 → Y</code>，接著在第一班結束的同一秒再跑相同路線；首班消耗 2，沒有在 Yard 等待充電，第二班離開 Y 會報電量不足。</p>
      </section>
      <p>完成示範後，先刪除示範 services，再刪除無引用的 DEMO 車輛。<a routerLink="/editor">回 Editor</a></p>
    } @else if (!error) { <p>讀取情境與 block 設定中…</p> }
  `,
})
export class Guide implements OnInit {
  private readonly api = inject(Api);
  private readonly change = inject(ChangeDetectorRef);
  schedule: Schedule | null = null;
  origin = '';
  afterB1 = '';
  error = '';
  async ngOnInit() {
    try {
      const [schedule, blocks] = await Promise.all([this.api.schedule(), this.api.blocks()]);
      this.schedule = schedule;
      this.origin = schedule.scenario_start_at.slice(0, 19);
      this.afterB1 = new Date(Date.parse(schedule.scenario_start_at) + blocks['B1'] * 1000)
        .toISOString().slice(0, 19);
    } catch (e) { this.error = errorText(e); }
    finally { this.change.markForCheck(); }
  }
}
