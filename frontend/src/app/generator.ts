import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, GenerateInput, GeneratePreview, Schedule, Vehicle, errorText } from './api';

@Component({
  selector: 'app-generator', standalone: true, imports: [FormsModule, RouterLink],
  template: `
    <h1>Auto Schedule Generator</h1>
    <p>Auto and manual are alternative schedules. Generating replaces <strong>all auto services</strong>, never manual services.</p>
    <p>Bounded heuristic: passenger-only Y → all stations → Y, 60-second dwell at every platform visit. Up to 128 services / 2,000 candidate trials; failure to find a schedule does not prove none exists.</p>
    @if (error) { <p role="alert" class="error">{{ error }}</p> }
    @if (schedule) {
      <p>Auto revision {{ schedule.revision }} · {{ schedule.status }} · {{ schedule.timelines.length }} saved services · Scenario begins {{ schedule.scenario_start_at }} (UTC)</p>
      <button type="button" [disabled]="busy" (click)="refresh()">Refresh auto &amp; vehicles</button>
      <section>
        <h2>Search inputs</h2>
        <fieldset><legend>Existing vehicles (select at least one)</legend>
          @for (vehicle of vehicles; track vehicle.id) {
            <label><input type="checkbox" [checked]="selected.has(vehicle.id)" [disabled]="busy" (change)="toggle(vehicle.id, $event)" /> {{ vehicle.id }} — {{ vehicle.name }}</label>
          }
        </fieldset>
        @if (!vehicles.length) { <p>Create a vehicle in <a routerLink="/editor">Editor</a> first.</p> }
        <p><label>Passenger range start (UTC) <input type="datetime-local" step="1" name="rangeStart" [(ngModel)]="rangeStart" /></label></p>
        <p><label>Passenger range end (UTC) <input type="datetime-local" step="1" name="rangeEnd" [(ngModel)]="rangeEnd" /></label></p>
        <p><label>Maximum wait per station (seconds) <input type="number" min="1" step="1" name="interval" [(ngModel)]="intervalSeconds" /></label></p>
        <button type="button" [disabled]="busy || !validInput()" (click)="previewGeneration()">Preview auto candidate</button>
      </section>
      @if (preview && previewFresh()) {
        <section>
          <h2>Auto candidate · base revision {{ preview.base_revision }}</h2>
          <p>{{ preview.complete ? 'Complete (not saved)' : 'Incomplete — cannot commit' }} · {{ preview.attempts }} evaluated candidates · {{ preview.candidate.timelines.length }} proposed services</p>
          <p>Candidate IDs are provisional; saved IDs may differ. Preview does not change the current auto schedule.</p>
          @if (preview.gaps.length) {
            <h3>Uncovered passenger wait windows</h3>
            <ul>@for (gap of preview.gaps; track $index) {
              <li class="error">{{ gap.station_id }}: {{ gap.start_inclusive ? '[' : '(' }}{{ gap.start }} → {{ gap.end }}{{ gap.end_inclusive ? ']' : ')' }}</li>
            }</ul>
          }
          @for (timeline of preview.candidate.timelines; track timeline.service.id) {
            <p>#{{ timeline.service.id }} · {{ timeline.service.vehicle_id }} · {{ timeline.service.start_at }} → {{ timeline.end_at }}</p>
          }
          <button type="button" [disabled]="busy || !preview.complete" (click)="commit()">Replace auto schedule</button>
          <button type="button" [disabled]="busy" (click)="discard()">Discard preview</button>
        </section>
      } @else if (preview) { <p>Inputs changed. Preview again before committing.</p> }
      @if (message) { <p role="status">{{ message }} <a routerLink="/viewer">Open Viewer</a> and select Auto to inspect the saved result.</p> }
    } @else if (!error) { <p>Loading…</p> }
  `,
})
export class Generator implements OnInit {
  private readonly api = inject(Api);
  private readonly change = inject(ChangeDetectorRef);
  schedule: Schedule | null = null;
  vehicles: Vehicle[] = [];
  selected = new Set<string>();
  rangeStart = '';
  rangeEnd = '';
  intervalSeconds = 600;
  preview: GeneratePreview | null = null;
  private previewInput = '';
  error = '';
  message = '';
  busy = false;

  async ngOnInit() { await this.refresh(); }
  async refresh() {
    this.busy = true;
    try {
      [this.schedule, this.vehicles] = await Promise.all([this.api.schedule('auto'), this.api.vehicles()]);
      this.selected = new Set([...this.selected].filter(id => this.vehicles.some(v => v.id === id)));
      if (!this.rangeStart) this.rangeStart = this.schedule.scenario_start_at.slice(0, 19);
      if (!this.rangeEnd) this.rangeEnd = this.rangeStart;
      this.discard();
      this.error = '';
    } catch (e) { this.error = errorText(e); }
    finally { this.busy = false; this.change.markForCheck(); }
  }
  toggle(id: string, event: Event) {
    if ((event.target as HTMLInputElement).checked) this.selected.add(id);
    else this.selected.delete(id);
    this.selected = new Set(this.selected);
  }
  validInput(): boolean {
    if (!this.schedule || !this.selected.size || !this.rangeStart || !this.rangeEnd ||
        !Number.isSafeInteger(this.intervalSeconds) || this.intervalSeconds <= 0) return false;
    const start = Date.parse(`${this.rangeStart}Z`);
    const end = Date.parse(`${this.rangeEnd}Z`);
    return Number.isFinite(start) && Number.isFinite(end) &&
      start >= Date.parse(this.schedule.scenario_start_at) && end >= start;
  }
  input(): GenerateInput {
    // datetime-local is explicitly interpreted as UTC, not browser local time.
    return { vehicle_ids: [...this.selected].sort(), start: `${this.rangeStart}Z`,
             end: `${this.rangeEnd}Z`, interval_seconds: this.intervalSeconds };
  }
  previewFresh(): boolean { return this.previewInput === JSON.stringify(this.input()); }
  discard() { this.preview = null; this.previewInput = ''; }
  async previewGeneration() {
    if (!this.validInput()) return;
    this.busy = true;
    this.discard(); this.message = '';
    try {
      const input = this.input();
      this.preview = await this.api.previewGeneration(input);
      this.previewInput = JSON.stringify(input);
      this.error = '';
    } catch (e) { this.error = errorText(e); }
    finally { this.busy = false; this.change.markForCheck(); }
  }
  async commit() {
    if (!this.preview?.complete || !this.previewFresh() || !this.validInput()) return;
    if (!confirm('Replace every service in the auto alternative? Manual services will not change.')) return;
    this.busy = true;
    try {
      const saved = await this.api.commitGeneration(this.input(), this.preview.base_revision);
      this.schedule = saved;
      this.discard(); this.error = '';
      this.message = `Saved ${saved.timelines.length} auto services at revision ${saved.revision}.`;
    } catch (e) {
      this.error = errorText(e);
      if ((e as { status?: number }).status === 409) {
        this.discard();
        try { this.schedule = await this.api.schedule('auto'); } catch { /* retain original error */ }
      }
    } finally { this.busy = false; this.change.markForCheck(); }
  }
}
