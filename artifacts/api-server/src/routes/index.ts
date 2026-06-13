import { Router, type IRouter } from "express";
import healthRouter from "./health";
import ctraderRouter from "./ctrader";

const router: IRouter = Router();

router.use(healthRouter);
router.use(ctraderRouter);

export default router;
