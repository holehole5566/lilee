import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

export interface Step { node: string; dwell_seconds: number }
export interface Service { id: number; vehicle_id: string; start_at: string; steps: Step[]; is_passenger_service: boolean }
export type ServiceInput = Omit<Service, 'id' | 'is_passenger_service'>;
export interface Visit { node: string; arrival: string; departure: string; battery: number | null }
export interface Timeline { service: Service; visits: Visit[]; end_at: string; blocks: { resource: string; start: string; end: string }[] }
export interface Conflict { code: string; message: string; service_ids: number[]; vehicle_ids: string[]; resource: string | null; start: string | null; end: string | null }
export interface Schedule { scenario: 'manual' | 'auto'; revision: number; scenario_start_at: string; status: 'validated' | 'draft'; timelines: Timeline[]; conflicts: Conflict[]; unknown_from: Record<string, string> }
export interface Track { nodes: Record<string, string>; edges: [string, string][]; stations: Record<string, string>; interlockings: Record<string, string[]> }
export interface Vehicle { id: string; name: string }
export interface BlockPreview { base_revision: number; base_auto_revision: number; affected_services: { service_id: number; old_end_at: string; new_end_at: string }[]; auto_affected_services: { service_id: number; old_end_at: string; new_end_at: string }[]; candidate: Schedule; auto_candidate: Schedule }
export interface ServicePreview { base_revision: number; provisional_service_id: number | null; candidate: Schedule }
export interface GenerateInput { vehicle_ids: string[]; start: string; end: string; interval_seconds: number }
export interface CoverageGap { station_id: string; start: string; end: string; start_inclusive: boolean; end_inclusive: boolean }
export interface GeneratePreview { base_revision: number; complete: boolean; attempts: number; exhausted: boolean; gaps: CoverageGap[]; candidate: Schedule }

@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);
  schedule(scenario: 'manual' | 'auto' = 'manual') {
    return firstValueFrom(this.http.get<Schedule>(`/api/schedule?scenario=${scenario}`));
  }
  track() { return firstValueFrom(this.http.get<Track>('/api/track')); }
  blocks() { return firstValueFrom(this.http.get<Record<string, number>>('/api/blocks')); }
  vehicles() { return firstValueFrom(this.http.get<Vehicle[]>('/api/vehicles')); }
  createVehicle(v: Vehicle) { return firstValueFrom(this.http.post<Vehicle>('/api/vehicles', v)); }
  renameVehicle(id: string, name: string) { return firstValueFrom(this.http.put<Vehicle>(`/api/vehicles/${encodeURIComponent(id)}`, { name })); }
  deleteVehicle(id: string) { return firstValueFrom(this.http.delete(`/api/vehicles/${encodeURIComponent(id)}`)); }
  previewService(operation: 'create' | 'update' | 'delete', service: ServiceInput | null, serviceId: number | null) {
    return firstValueFrom(this.http.post<ServicePreview>('/api/schedule/validate', {
      operation, service, service_id: serviceId,
    }));
  }
  createService(s: ServiceInput) { return firstValueFrom(this.http.post<Schedule>('/api/services', s)); }
  updateService(id: number, s: ServiceInput) { return firstValueFrom(this.http.put<Schedule>(`/api/services/${id}`, s)); }
  deleteService(id: number) { return firstValueFrom(this.http.delete<Schedule>(`/api/services/${id}`)); }
  previewGeneration(input: GenerateInput) {
    return firstValueFrom(this.http.post<GeneratePreview>('/api/schedule/generate/preview', input));
  }
  commitGeneration(input: GenerateInput, revision: number) {
    return firstValueFrom(this.http.post<Schedule>('/api/schedule/generate/commit', {
      ...input, expected_revision: revision,
    }));
  }
  previewBlock(id: string, seconds: number) { return firstValueFrom(this.http.post<BlockPreview>(`/api/blocks/${id}/preview`, { traversal_seconds: seconds })); }
  updateBlock(id: string, seconds: number, revision: number, autoRevision: number) {
    return firstValueFrom(this.http.put<Schedule>(`/api/blocks/${id}`, {
      traversal_seconds: seconds, expected_revision: revision, expected_auto_revision: autoRevision,
    }));
  }
}

export function errorText(e: unknown): string {
  const detail = (e as { error?: { detail?: { code?: string; message?: string } } })?.error?.detail;
  return detail?.message ? `${detail.code}: ${detail.message}` : 'Request failed. Check the API connection and try again.';
}
