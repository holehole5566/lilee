import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Api, Schedule, Track, errorText } from './api';
import { Playback } from './playback';

@Component({
  selector: 'app-viewer', standalone: true, imports: [RouterLink, Playback],
  template: `
    <h1>Schedule Viewer <small>(read-only)</small></h1>
    <nav aria-label="Schedule scenario">
      <button type="button" [disabled]="scenario === 'manual'" (click)="selectScenario('manual')">Manual</button>
      <button type="button" [disabled]="scenario === 'auto'" (click)="selectScenario('auto')">Auto (alternative)</button>
    </nav>
    <p>These are alternative schedules, not vehicles operating at the same time. <a routerLink="/generator">Generate the auto alternative</a>.</p>
    @if (error) { <p role="alert" class="error">{{ error }}</p> }
    @if (schedule) {
      <p>{{ schedule.scenario === 'manual' ? 'Manual' : 'Auto' }} scenario · Revision {{ schedule.revision }} · <strong [class.error]="schedule.status === 'draft'">{{ schedule.status }}</strong>
        · Scenario start: {{ schedule.scenario_start_at }} UTC</p>
      <button type="button" (click)="refresh()">Refresh</button>
      @if (trackData) { <app-playback [schedule]="schedule" [track]="trackData" /> }
      @if (!schedule.timelines.length) { <p>No scheduled services.</p> }
      @for (t of schedule.timelines; track t.service.id) {
        <section>
          <h2>#{{ t.service.id }} · {{ t.service.vehicle_id }}</h2>
          <p>{{ t.service.start_at }} → {{ t.end_at }}</p>
          <table><thead><tr><th>Node</th><th>Arrival (UTC)</th><th>Departure (UTC)</th><th>Battery after visit</th></tr></thead>
            <tbody>@for (v of t.visits; track $index) {
              <tr><td>{{ v.node }}</td><td>{{ v.arrival }}</td><td>{{ v.departure }}</td>
                <td>{{ v.battery === null ? 'Unknown' : v.battery.toFixed(2) }}</td></tr>
            }</tbody>
          </table>
        </section>
      }
      @if (schedule.conflicts.length) {
        <section><h2>Conflicts — draft is not runnable</h2>
          <ul>@for (c of schedule.conflicts; track $index) {
            <li class="error">{{ c.code }}: {{ c.message }} ({{ c.service_ids.join(', ') }}, {{ c.resource }}; {{ c.start }} → {{ c.end }})</li>
          }</ul>
        </section>
      }
    } @else if (!error) { <p>Loading…</p> }
  `,
})
export class Viewer implements OnInit {
  private readonly api = inject(Api);
  private readonly change = inject(ChangeDetectorRef);
  schedule: Schedule | null = null;
  trackData: Track | null = null;
  scenario: 'manual' | 'auto' = 'manual';
  error = '';
  async ngOnInit() { await this.refresh(); }
  async selectScenario(scenario: 'manual' | 'auto') {
    this.scenario = scenario;
    this.schedule = null;
    await this.refresh();
  }
  async refresh() {
    try {
      [this.schedule, this.trackData] = await Promise.all([this.api.schedule(this.scenario), this.api.track()]);
      this.error = '';
    }
    catch (e) { this.error = errorText(e); }
    finally { this.change.markForCheck(); }
  }
}
