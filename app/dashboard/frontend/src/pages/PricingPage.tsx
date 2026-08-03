import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, AlertDescription } from "@appica/ui-react/alert";
import { Button } from "@appica/ui-react/button";
import { Input } from "@appica/ui-react/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@appica/ui-react/table";
import { api } from "../api";
import PageIntro from "../components/PageIntro";

export default function PricingPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["pricing"], queryFn: api.pricing });
  const [form, setForm] = useState({ provider: "*", model_pattern: "", input_price: "0", output_price: "0" });
  const create = useMutation({ mutationFn: () => api.createPricing({ ...form, input_price: Number(form.input_price), output_price: Number(form.output_price), source: "manual" }), onSuccess: () => { setForm({ ...form, model_pattern: "" }); qc.invalidateQueries({ queryKey: ["pricing"] }); } });
  const sync = useMutation({ mutationFn: api.syncPricing, onSuccess: () => qc.invalidateQueries({ queryKey: ["pricing"] }) });
  return (
    <div className="page-stack">
      <PageIntro
        eyebrow="Pricing registry"
        title="模型与定价"
        description="价格单位为 microusd / 1M tokens；历史调用绑定价格版本，不回溯修改。"
        actions={<Button variant="outline" disabled={sync.isPending} onClick={() => sync.mutate()}>{sync.isPending ? "同步中…" : "同步 LiteLLM 价格"}</Button>}
      />
      <section className="card pricing-create-form">
        <div className="form-section-head"><div><p className="section-kicker">Immutable version</p><h3>新建定价版本</h3></div></div>
        <div className="pricing-form-grid"><Input aria-label="Provider" placeholder="Provider" value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })} /><Input aria-label="模型模式" placeholder="模型模式，例如 gpt-*" value={form.model_pattern} onChange={(event) => setForm({ ...form, model_pattern: event.target.value })} /><Input aria-label="输入价" type="number" placeholder="输入价" value={form.input_price} onChange={(event) => setForm({ ...form, input_price: event.target.value })} /><Input aria-label="输出价" type="number" placeholder="输出价" value={form.output_price} onChange={(event) => setForm({ ...form, output_price: event.target.value })} /><Button disabled={create.isPending || !form.model_pattern.trim()} onClick={() => create.mutate()}>{create.isPending ? "创建中…" : "创建版本"}</Button></div>
      </section>
      {create.isError ? <Alert variant="error"><AlertDescription>{(create.error as Error).message}</AlertDescription></Alert> : null}
      <div className="card table-surface">
        <Table size="sm" borderStyle="none" hoverableRows>
          <TableHeader>
            <TableRow>
              <TableHead>Provider</TableHead>
              <TableHead>模型模式</TableHead>
              <TableHead>输入价</TableHead>
              <TableHead>输出价</TableHead>
              <TableHead>缓存输入价</TableHead>
              <TableHead>推理价</TableHead>
              <TableHead>版本</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(q.data?.items || []).map((item: any) => (
              <TableRow key={item.id}>
                <TableCell>{item.provider}</TableCell>
                <TableCell>{item.model_pattern}</TableCell>
                <TableCell>{item.input_price}</TableCell>
                <TableCell>{item.output_price}</TableCell>
                <TableCell>{item.cached_input_price}</TableCell>
                <TableCell>{item.reasoning_price}</TableCell>
                <TableCell>{item.version}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
