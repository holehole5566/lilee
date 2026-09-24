import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Api, Schedule, ServiceInput, ServicePreview, Step, Track, Vehicle, errorText } from './api';
import { TrackMap } from './track-map';

@Component({
  selector: 'app-editor', standalone: true, imports: [CommonModule, FormsModule, TrackMap],
  template: `
    <h1>Schedule Editor</h1>
    <p>Manual scenario only. Automatic schedules are separate alternatives and cannot be edited here.</p>
    @if (error) { <p role="alert" class="error">{{ error }}</p> }
    @if (schedule) {
      <p>Revision {{ schedule.revision }} · <strong [class.error]="schedule.status === 'draft'">{{ schedule.status }}</strong>
        · Scenario begins {{ schedule.scenario_start_at }} (UTC)</p>
      <section><h2>Vehicles</h2>
        <form (ngSubmit)="addVehicle()">
          <label>ID <input name="newId" [(ngModel)]="newVehicleId" required maxlength="40" /></label>
          <label>Name <input name="newName" [(ngModel)]="newVehicleName" required /></label>
          <button [disabled]="busy">Add vehicle</button>
        </form>
        <ul>@for (vehicle of vehicles; track vehicle.id) {
          <li>{{ vehicle.id }} — {{ vehicle.name }}
            <button type="button" (click)="rename(vehicle)" [disabled]="busy">Rename</button>
            <button type="button" (click)="removeVehicle(vehicle.id)" [disabled]="busy">Delete</button>
          </li>
        }</ul>
      </section>
      <section><h2>{{ editingId === null ? 'New service' : 'Edit service #' + editingId }}</h2>
        <form (ngSubmit)="save()">
          <label>Vehicle
            <select name="vehicle" [(ngModel)]="vehicleId" required>
              <option value="">Select vehicle</option>
              @for (v of vehicles; track v.id) { <option [value]="v.id">{{ v.id }} — {{ v.name }}</option> }
            </select>
          </label>
          <label>Start (UTC) <input name="start" type="datetime-local" step="1" [(ngModel)]="startAt" required /></label>
          <div><h3>Path (directed)</h3>
            <p>Click a dotted-ring node to append it; amber highlights the current path. The dropdown below uses the same directed choices.</p>
            @if (trackData) {
              <app-track-map [track]="trackData" [path]="pathNodes()" [selectable]="choices()"
                             (nodeSelected)="appendNode($event)" />
            }
            <ol>@for (step of steps; track $index) {
              <li>{{ step.node }}
                @if (trackData?.nodes?.[step.node] === 'PLATFORM' || step.node === 'Y') {
                  <label>{{ step.node === 'Y' ? 'Charge / dwell (s)' : 'Dwell (s)' }}
                    <input type="number" min="0" [name]="'dwell' + $index" [(ngModel)]="step.dwell_seconds" />
                  </label>
                }
              </li>
            }</ol>
            <label>Next node
              <select name="next" [(ngModel)]="nextNode">
                <option value="">Choose next</option>
                @for (node of choices(); track node) { <option [value]="node">{{ node }}</option> }
              </select>
            </label>
            <button type="button" (click)="append()" [disabled]="!nextNode">Add to path</button>
            <button type="button" (click)="steps.pop()" [disabled]="steps.length === 0">Undo last</button>
            <button type="button" (click)="steps = []; nextNode = ''">Clear</button>
            <p class="muted">Start/end at Y or a platform; use directed adjacency. Repeated nodes remain separate steps. Yard dwell charges at 1 unit per 12 seconds (max 100).</p>
          </div>
          @if (invalidEnd()) { <p role="alert" class="error">Path ends at a block. Add a Yard or platform stop before this service can be saved.</p> }
          @if (previewError && previewErrorFresh() && !invalidEnd()) {
            <p role="alert" class="error">Preview failed: {{ previewError }}. Not saved.</p>
          }
          <button type="button" (click)="previewService()" [disabled]="busy || !vehicleId || steps.length < 2">Preview change</button>
          <button [disabled]="busy || !vehicleId || steps.length < 2 || invalidEnd()">{{ editingId === null ? 'Create' : 'Update' }}</button>
          <button type="button" (click)="reset()">Cancel / reset</button>
          @if (preview && previewFresh()) {
            <section><h3>Candidate preview · base revision {{ preview.base_revision }}</h3>
              <p><strong [class.error]="preview.candidate.status === 'draft'">{{ preview.candidate.status }}</strong> · Not saved. Re-preview if the schedule changed. Yard battery is shown after its dwell.</p>
              @for (t of preview.candidate.timelines; track t.service.id) {
                @if (t.service.id === (editingId ?? preview.provisional_service_id)) {
                  <p>#{{ t.service.id }} (preview ID may change on save) · {{ t.service.start_at }} → {{ t.end_at }}</p>
                  <ul>@for (v of t.visits; track $index) {
                    <li>{{ v.node }}: arrival {{ v.arrival }}, departure {{ v.departure }} · battery {{ v.battery === null ? 'unknown' : v.battery }}</li>
                  }</ul>
                }
              }
              <ul>@for (c of preview.candidate.conflicts; track $index) {
                <li class="error">{{ c.code }}: {{ c.message }} · service {{ c.service_ids.join(', ') }}</li>
              }</ul>
            </section>
          }
        </form>
      </section>
      <section><h2>Services</h2>
        @if (!schedule.timelines.length) { <p>No services yet.</p> }
        @for (t of schedule.timelines; track t.service.id) {
          <article><strong>#{{ t.service.id }} · {{ t.service.vehicle_id }}</strong> · {{ t.service.start_at }} → {{ t.end_at }}
            <p>{{ path(t.service.steps) }}</p>
            <button type="button" (click)="edit(t.service)" [disabled]="busy">Edit</button>
            <button type="button" (click)="removeService(t.service.id)" [disabled]="busy">Delete</button>
          </article>
        }
        @if (schedule.conflicts.length) {
          <h3>Draft conflicts</h3><ul>@for (c of schedule.conflicts; track $index) {
            <li class="error">{{ c.code }} · {{ c.message }} · service {{ c.service_ids.join(', ') }} · {{ c.resource }}</li>
          }</ul>
        }
      </section>
    } @else if (!error) { <p>Loading…</p> }
  `,
})
export class Editor implements OnInit {
  private readonly api = inject(Api);
  private readonly change = inject(ChangeDetectorRef);
  schedule: Schedule | null = null;
  trackData: Track | null = null;
  vehicles: Vehicle[] = [];
  error = '';
  busy = false;
  newVehicleId = '';
  newVehicleName = '';
  editingId: number | null = null;
  vehicleId = '';
  startAt = '';
  steps: Step[] = [];
  nextNode = '';
  preview: ServicePreview | null = null;
  previewError = '';
  previewErrorInput = '';
  private previewInput = '';

  async ngOnInit() { await this.refresh(); }
  async refresh() {
    try {
      [this.schedule, this.trackData, this.vehicles] = await Promise.all([
        this.api.schedule(), this.api.track(), this.api.vehicles(),
      ]);
      if (!this.startAt) this.startAt = this.schedule.scenario_start_at.slice(0, 19);
      this.error = '';
    } catch (e) { this.error = errorText(e); }
    finally { this.change.markForCheck(); }
  }
  path(steps: Step[]) { return steps.map(s => s.node).join(' → '); }
  choices(): string[] {
    if (!this.trackData) return [];
    if (!this.steps.length) return Object.keys(this.trackData.nodes).filter(n => this.trackData!.nodes[n] !== 'BLOCK').sort();
    const last = this.steps[this.steps.length - 1].node;
    return this.trackData.edges.filter(([a]) => a === last).map(([, b]) => b).sort();
  }
  pathNodes() { return this.steps.map(s => s.node); }
  invalidEnd(): boolean {
    return this.steps.length >= 2 && this.trackData?.nodes[this.steps[this.steps.length - 1].node] === 'BLOCK';
  }
  appendNode(node: string) {
    if (!this.choices().includes(node)) return;
    this.steps.push({ node, dwell_seconds: 0 });
    this.nextNode = '';
  }
  append() { this.appendNode(this.nextNode); }
  reset() {
    this.editingId = null; this.vehicleId = '';
    this.preview = null;
    this.steps = []; this.nextNode = '';
    this.startAt = this.schedule?.scenario_start_at.slice(0, 19) ?? '';
  }
  edit(service: { id: number; vehicle_id: string; start_at: string; steps: Step[] }) {
    this.editingId = service.id;
    this.preview = null;
    this.vehicleId = service.vehicle_id;
    this.startAt = service.start_at.slice(0, 19);
    this.steps = service.steps.map(s => ({ ...s }));
    this.nextNode = '';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  async action(operation: () => Promise<unknown>) {
    this.busy = true;
    try { await operation(); await this.refresh(); }
    catch (e) { this.error = errorText(e); }
    finally { this.busy = false; this.change.markForCheck(); }
  }
  input(): ServiceInput {
    // datetime-local has no timezone; the form explicitly represents UTC, not browser local time.
    return {
      vehicle_id: this.vehicleId, start_at: `${this.startAt}Z`,
      steps: this.steps.map(s => ({ ...s })),
    };
  }
  previewFresh() { return this.previewInput === JSON.stringify(this.input()); }
  previewErrorFresh() { return this.previewErrorInput === JSON.stringify(this.input()); }
  async previewService() {
    this.busy = true;
    try {
      const input = this.input();
      this.preview = await this.api.previewService(
        this.editingId === null ? 'create' : 'update', input, this.editingId);
      this.previewInput = JSON.stringify(input);
      this.previewError = ''; this.error = '';
    } catch (e) {
      this.preview = null; this.previewError = errorText(e);
      this.previewErrorInput = JSON.stringify(this.input());
    }
    finally { this.busy = false; this.change.markForCheck(); }
  }
  async save() {
    const input = this.input();
    let saved = false;
    await this.action(async () => {
      if (this.editingId === null) await this.api.createService(input);
      else await this.api.updateService(this.editingId, input);
      saved = true;
    });
    if (saved) this.reset();
  }
  async removeService(id: number) {
    if (confirm(`Delete service #${id}?`)) await this.action(() => this.api.deleteService(id));
  }
  async addVehicle() {
    await this.action(() => this.api.createVehicle({ id: this.newVehicleId.trim(), name: this.newVehicleName.trim() }));
    if (!this.error) { this.newVehicleId = ''; this.newVehicleName = ''; }
  }
  async rename(vehicle: Vehicle) {
    const name = prompt(`New name for ${vehicle.id}`, vehicle.name);
    if (name?.trim()) await this.action(() => this.api.renameVehicle(vehicle.id, name.trim()));
  }
  async removeVehicle(id: string) {
    if (confirm(`Delete vehicle ${id}?`)) await this.action(() => this.api.deleteVehicle(id));
  }
}
