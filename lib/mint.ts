// Token-2022 mint state, decoded from the raw account bytes through the app's own RPC. Mirrors keeper/keeper.py.
import { PublicKey } from "@solana/web3.js";
import { rpc } from "@/lib/chain";

export const TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
export type MintState = { decimals: number; multiplier: string; paused: boolean; hookProgram: string | null; permanentDelegate: string | null; extensions: number[] };

export function decodeMintState(raw: Buffer, nowTs: number): MintState {
  const st: MintState = { decimals: raw[44], multiplier: "1", paused: false, hookProgram: null, permanentDelegate: null, extensions: [] }; // decimals byte at offset 44 of the base mint
  let i = 166;
  while (i + 4 <= raw.length) {
    const t = raw.readUInt16LE(i), n = raw.readUInt16LE(i + 2); i += 4;
    if (t === 0) break;
    const body = raw.subarray(i, i + n); i += n;
    st.extensions.push(t);
    if (t === 25 && n >= 56) { // ScaledUiAmount: authority 32, multiplier f64, effective ts i64, new multiplier f64
      const mult = body.readDoubleLE(32), eff = Number(body.readBigInt64LE(40)), nm = body.readDoubleLE(48);
      st.multiplier = String(eff && nowTs >= eff ? nm : mult);
    } else if (t === 26 && n >= 33) st.paused = body[32] !== 0;
    else if (t === 14 && n >= 64) { const prog = body.subarray(32, 64); st.hookProgram = prog.every((b) => b === 0) ? null : new PublicKey(prog).toBase58(); }
    else if (t === 12 && n >= 32) st.permanentDelegate = new PublicKey(body.subarray(0, 32)).toBase58();
  }
  return st;
}

export async function readMintState(mint: string): Promise<MintState> {
  const res = await rpc<{ value: { owner: string; data: [string, string] } | null }>("getAccountInfo", [mint, { encoding: "base64", commitment: "confirmed" }]);
  const info = res?.value;
  if (!info || info.owner !== TOKEN_2022) throw new Error(`${mint} is not a Token-2022 mint`);
  return decodeMintState(Buffer.from(info.data[0], "base64"), Math.floor(Date.now() / 1000));
}
