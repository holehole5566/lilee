import { ChangeDetectorRef, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, BlockPreview, errorText } from './api';

@Component({
  selector: 'app-configuration', standalone: true, imports: [FormsModule],
  styles: [`
    .block-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(235px, 1fr)); gap: 1rem; margin-top: 1.5rem; }
    .block-card { margin: 0; padding: 1.1rem 1.2rem; border-top: 4px solid #61a5a0; }
    .block-card:nth-child(3n + 2) { border-top-color: #e4ae56; }
    .block-card:nth-child(3n) { border-top-color: #e98270; }
    .block-card h2 { display: flex; align-items: baseline; gap: .5rem; margin: 0 0 .7rem; font-size: 1.3rem; }
    .block-card h2 small { font-family: system-ui, sans-serif; font-size: .64rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
    .block-card label { display: flex; margin: 0 0 .75rem; }
    .block-card input { max-width: none; width: 100%; }
    .block-card button { width: 100%; margin: 0; }
  `],
  template: `
    <h1>Block Configuration</h1>
    @if (error) { <p role="alert" class="error">{{ error }}</p> }
    @if (blocks.length) {
      <p>Block durations are shared by the manual and auto alternative scenarios. Preview both before saving: each may become a draft.</p>
      <div class="block-grid">
        @for (block of blocks; track block.id) {
          <section class="block-card">
            <h2>{{ block.id }} <small>Track segment</small></h2>
            <label>Traversal seconds
              <input type="number" min="1" [name]="block.id" [(ngModel)]="block.seconds" (ngModelChange)="discardPreview()" />
            </label>
            <button type="button" [disabled]="busy || block.seconds < 1" (click)="previewBlock(block.id, block.seconds)">Preview</button>
          </section>
        }
      </div>
      @if (preview && pendingId) {
        <section>
          <h2>Preview {{ pendingId }} · manual revision {{ preview.base_revision }} · auto revision {{ preview.base_auto_revision }}</h2>
          <h3>Manual: <strong [class.error]="preview.candidate.status === 'draft'">{{ preview.candidate.status }}</strong>
             · {{ preview.candidate.conflicts.length }} conflicts</h3>
          @if (!preview.affected_services.length) { <p>No manual service time changes.</p> }
          <ul>@for (c of preview.affected_services; track c.service_id) {
            <li>#{{ c.service_id }}: {{ c.old_end_at }} → {{ c.new_end_at }}</li>
          }</ul>
          <ul>@for (c of preview.candidate.conflicts; track $index) {
            <li class="error">{{ c.code }}: {{ c.message }} · {{ c.service_ids.join(', ') }}</li>
          }</ul>
          <h3>Auto: <strong [class.error]="preview.auto_candidate.status === 'draft'">{{ preview.auto_candidate.status }}</strong>
             · {{ preview.auto_candidate.conflicts.length }} conflicts</h3>
          @if (!preview.auto_affected_services.length) { <p>No auto service time changes.</p> }
          <ul>@for (c of preview.auto_affected_services; track c.service_id) {
            <li>#{{ c.service_id }}: {{ c.old_end_at }} → {{ c.new_end_at }}</li>
          }</ul>
          <ul>@for (c of preview.auto_candidate.conflicts; track $index) {
            <li class="error">{{ c.code }}: {{ c.message }} · {{ c.service_ids.join(', ') }}</li>
          }</ul>
          <button type="button" (click)="commit()" [disabled]="busy">Confirm and save</button>
          <button type="button" (click)="discardPreview()">Cancel</button>
        </section>
      }
    } @else if (!error) { <p>Loading…</p> }
  `,
})
export class Configuration implements OnInit {
  private readonly api = inject(Api);
  private readonly change = inject(ChangeDetectorRef);
  blocks: { id: string; seconds: number }[] = [];
  preview: BlockPreview | null = null;
  pendingId = '';
  pendingSeconds = 0;
  error = '';
  busy = false;
  async ngOnInit() { await this.refresh(); }
  async refresh() {
    try {
      const blocks = await this.api.blocks();
      this.blocks = Object.entries(blocks).sort(([a], [b]) => Number(a.slice(1)) - Number(b.slice(1)))
        .map(([id, seconds]) => ({ id, seconds }));
      this.error = '';
    } catch (e) { this.error = errorText(e); }
    finally { this.change.markForCheck(); }
  }
  discardPreview() { this.preview = null; this.pendingId = ''; }
  async previewBlock(id: string, seconds: number) {
    this.busy = true;
    try {
      this.preview = await this.api.previewBlock(id, seconds);
      this.pendingId = id; this.pendingSeconds = seconds; this.error = '';
    } catch (e) { this.error = errorText(e); this.discardPreview(); }
    finally { this.busy = false; this.change.markForCheck(); }
  }
  async commit() {
    if (!this.preview || !this.pendingId) return;
    this.busy = true;
    try {
      await this.api.updateBlock(this.pendingId, this.pendingSeconds,
                                 this.preview.base_revision, this.preview.base_auto_revision);
      this.discardPreview(); await this.refresh();
    } catch (e) {
      this.error = errorText(e);
      if ((e as { status?: number }).status === 409) this.discardPreview();
    } finally { this.busy = false; this.change.markForCheck(); }
  }
}
